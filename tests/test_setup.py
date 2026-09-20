import json

import pytest

from lapbar import auth, config, setup, vault
from lapbar.http import HttpError

GOOD_ID = "12345"
GOOD_SECRET = "a" * 40


class Script:
    """Feeds scripted answers to input()/getpass() and records everything printed."""

    def __init__(self, inputs=(), secrets=()):
        self.inputs, self.secrets, self.lines, self.opened = list(inputs), list(secrets), [], []

    def input(self, prompt=""):
        self.lines.append(prompt)
        return self.inputs.pop(0)

    def secret(self, prompt=""):
        self.lines.append(prompt)
        return self.secrets.pop(0)

    def out(self, text=""):
        self.lines.append(text)

    @property
    def text(self):
        return "\n".join(self.lines)


def fake_authorize(record=None, fail=None):
    def go(client_id, client_secret, *, open_browser=True, timeout=300, out=print):
        if record is not None:
            record.append((client_id, client_secret, open_browser))
        if fail:
            raise fail
        auth._save_tokens({"access_token": "a", "refresh_token": "r", "expires_at": 9})
        out("Signed in.")
    return go


def run(script, **kw):
    kw.setdefault("authorize", fake_authorize())
    kw.setdefault("verify", lambda: "Ana Kovac")
    return setup.run(input_fn=script.input, secret_fn=script.secret, out=script.out,
                     open_url=script.opened.append, **kw)


def test_fresh_install_walks_through_all_three_steps(keyring):
    s = Script(inputs=["", GOOD_ID], secrets=[GOOD_SECRET])   # Enter to open the page, then the id
    calls = []
    assert run(s, authorize=fake_authorize(calls)) == 0
    text = s.text
    # the instructions a new user needs are on screen
    for must in ("https://www.strava.com/settings/api", "subscription", "localhost", "Visualizer",
                 "Authorization Callback Domain", "Client Secret", "Authorize"):
        assert must in text, must
    assert s.opened == [setup.API_SETTINGS_URL]
    # credentials landed in the right places
    assert config.get("STRAVA_CLIENT_ID") == GOOD_ID
    assert keyring.items["client_secret"] == GOOD_SECRET
    assert "STRAVA_CLIENT_SECRET" not in config.user_env_path().read_text()
    assert calls == [(GOOD_ID, GOOD_SECRET, True)]
    assert "Signed in as Ana Kovac" in text and "All set" in text


def test_the_secret_is_never_echoed(keyring):
    s = Script(inputs=["s", GOOD_ID], secrets=[GOOD_SECRET])
    run(s)
    assert GOOD_SECRET not in s.text


def test_skipping_the_browser_does_not_open_it(keyring):
    s = Script(inputs=["s", GOOD_ID], secrets=[GOOD_SECRET])
    run(s)
    assert s.opened == []


def test_no_browser_mode_never_opens_it_and_tells_authorize(keyring):
    s = Script(inputs=["", GOOD_ID], secrets=[GOOD_SECRET])
    calls = []
    run(s, open_browser=False, authorize=fake_authorize(calls))
    assert s.opened == [] and calls[0][2] is False


def test_bad_id_and_secret_are_explained_and_retried(keyring):
    s = Script(inputs=["", "abc", "12", GOOD_ID], secrets=["too-short", "z" * 40, GOOD_SECRET])
    assert run(s) == 0
    assert "does not look like a Client ID" in s.text
    assert "does not look like a Client Secret" in s.text
    assert keyring.items["client_secret"] == GOOD_SECRET


def test_quitting_at_any_prompt_is_clean(keyring):
    s = Script(inputs=["", "q"])
    assert run(s) == 130
    assert "cancelled" in s.text.lower()
    assert "client_secret" not in keyring.items


def test_giving_up_after_repeated_bad_input(keyring):
    s = Script(inputs=["", "x", "y", "z"])
    assert run(s) == 130


def test_rejected_credentials_offer_to_enter_them_again(keyring):
    s = Script(inputs=["", GOOD_ID, GOOD_ID], secrets=["b" * 40, GOOD_SECRET])
    outcomes = iter([HttpError(401, "invalid"), None])

    def authorize(client_id, client_secret, **kw):
        err = next(outcomes)
        if err:
            raise err
        auth._save_tokens({"access_token": "a", "refresh_token": "r", "expires_at": 9})

    assert run(s, authorize=authorize) == 0
    assert "rejected the Client ID" in s.text and "not an Access Token" in s.text
    assert keyring.items["client_secret"] == GOOD_SECRET   # the corrected one is kept


def test_callback_timeout_points_at_the_usual_cause(keyring):
    s = Script(inputs=["", GOOD_ID], secrets=[GOOD_SECRET])
    err = auth.AuthTimeout("Nothing came back from Strava. Check the Authorization Callback Domain 'localhost'")
    assert run(s, authorize=fake_authorize(fail=err)) == 1
    assert "Callback Domain" in s.text and "lapbar setup" in s.text


def set_up(keyring):
    config.write_env_file(config.user_env_path(), {"STRAVA_CLIENT_ID": GOOD_ID})
    keyring.items["client_secret"] = GOOD_SECRET
    keyring.items["tokens"] = json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": 9})


def test_already_set_up_offers_a_menu_and_enter_keeps_everything(keyring):
    set_up(keyring)
    s = Script(inputs=[""])
    assert run(s) == 0
    for must in ("set up and signed in as", "Ana Kovac", "Update the Client Secret", "Sign in with Strava again"):
        assert must in s.text, must
    assert s.secrets == [] and keyring.items["client_secret"] == GOOD_SECRET


def test_updating_just_the_secret(keyring):
    set_up(keyring)
    new = "b" * 40
    s = Script(inputs=["2"], secrets=[new])
    assert run(s) == 0
    assert keyring.items["client_secret"] == new
    assert "new secret works" in s.text and GOOD_ID in config.user_env_path().read_text()


def test_updating_the_secret_can_require_signing_in_again(keyring):
    set_up(keyring)
    calls = []
    answers = iter(["Ana Kovac"])

    def verify():
        try:
            return next(answers)
        except StopIteration:
            raise HttpError(401, "expired")

    # first verify() (menu) succeeds, the one after the update fails -> sign in again
    s = Script(inputs=["2"], secrets=["c" * 40])
    assert run(s, verify=verify, authorize=fake_authorize(calls)) == 0
    assert calls and "sign in again with the new secret" in s.text


def test_signing_in_again_from_the_menu(keyring):
    set_up(keyring)
    calls = []
    s = Script(inputs=["4"])
    assert run(s, authorize=fake_authorize(calls)) == 0
    assert calls == [(GOOD_ID, GOOD_SECRET, True)]


def test_removing_everything_needs_an_explicit_yes(keyring):
    set_up(keyring)
    assert run(Script(inputs=["5", "no"])) == 0
    assert "client_secret" in keyring.items                      # declined: nothing removed
    assert run(Script(inputs=["5", "yes"])) == 0
    assert keyring.items == {}


def test_hidden_secret_entry_is_acknowledged_without_showing_it(keyring):
    s = Script(inputs=["s", GOOD_ID], secrets=[GOOD_SECRET])
    run(s)
    assert "Nothing appears while you type" in s.text
    assert "nothing to copy" in s.text                  # explains why no token is asked for
    assert "Received a Client Secret (40 characters, hidden)" in s.text
    assert GOOD_SECRET not in s.text


def test_credentials_but_no_signin_goes_straight_to_signing_in(keyring):
    config.write_env_file(config.user_env_path(), {"STRAVA_CLIENT_ID": GOOD_ID})
    keyring.items["client_secret"] = GOOD_SECRET
    s = Script()
    calls = []
    assert run(s, authorize=fake_authorize(calls)) == 0
    assert calls == [(GOOD_ID, GOOD_SECRET, True)] and "Step 1" not in s.text


def test_without_a_keyring_the_fallback_is_disclosed(keyring):
    keyring.broken = "Cannot autolaunch D-Bus"
    s = Script(inputs=["s", GOOD_ID], secrets=[GOOD_SECRET])
    assert run(s) == 0
    assert "No system keyring was found" in s.text and "only your" in s.text
    assert (config.state_dir() / "secrets.json").exists()


def test_reset_forgets_everything(keyring):
    config.write_env_file(config.user_env_path(), {"STRAVA_CLIENT_ID": GOOD_ID, "OTHER": "keep"})
    keyring.items.update(client_secret=GOOD_SECRET, tokens="{}")
    config.private_dir(config.cache_path().parent)
    config.cache_path().write_text("{}")
    removed = setup.forget(out=lambda *_: None)
    assert keyring.items == {}
    assert not config.cache_path().exists()
    assert config.user_env_path().read_text().strip() == "OTHER=keep"
    assert {"client secret", "tokens", "client id"} <= set(removed)


def test_reset_also_removes_the_downloaded_time_series(keyring):
    from lapbar import streams
    streams._store(1, {"empty": True})
    streams._store(2, {"v": 2})
    removed = setup.forget(out=lambda *_: None)
    assert "downloaded charts" in removed and not streams._dir().exists()


def test_reset_deletes_the_config_file_when_only_comments_remain(keyring):
    config.write_env_file(config.user_env_path(), {"STRAVA_CLIENT_ID": GOOD_ID})
    config.user_env_path().write_text("# notes\n" + config.user_env_path().read_text())
    setup.forget(out=lambda *_: None)
    assert not config.user_env_path().exists()


@pytest.mark.parametrize("text,ok", [("12345", True), ("123456789", True), ("12", False), ("12a45", False), ("", False)])
def test_client_id_validation(text, ok):
    assert setup.valid_client_id(text) is ok


@pytest.mark.parametrize("text,ok", [("a" * 40, True), ("A1" * 20, True), ("a" * 39, False), ("g" * 40, False)])
def test_client_secret_validation(text, ok):
    assert setup.valid_client_secret(text) is ok
