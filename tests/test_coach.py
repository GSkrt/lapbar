"""Nudges to get off the chair: data-based, quiet in recovery, paused on request, capped, and never about bodies."""
import json
import re
import stat
from datetime import datetime, timedelta

import pytest

from lapbar import cli, coach, excuses, prefs

NOW = datetime(2026, 9, 20, 12, 0)


def settings(**over):
    return {**prefs.DEFAULTS, "coach_tone": "motivational", **over}


def series(n=60, fitness=30.0, form=0.0, load=50.0):
    """A fitness series of n days; each argument is a number or a list (the last values apply to the last days)."""
    def col(v):
        return list(v) if isinstance(v, (list, tuple)) else [v] * n
    f, fo, ld = col(fitness), col(form), col(load)
    pad = lambda c: [c[0]] * (n - len(c)) + c            # noqa: E731
    f, fo, ld = pad(f), pad(fo), pad(ld)
    return [{"date": (NOW - timedelta(days=n - 1 - i)).date().isoformat(), "load": ld[i], "fitness": f[i],
             "fatigue": f[i] - fo[i], "form": fo[i]} for i in range(n)]


def summary(idle=0, days=None, status="balanced", warming=False, change28=0, hours_ago=None):
    started = (NOW - timedelta(hours=hours_ago)) if hours_ago is not None else NOW - timedelta(days=idle, hours=4)
    days = days or series()
    return {"latest": {"start": started.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "fitness": {"days": days, "warming_up": warming, "fitness_change_28d_pct": change28,
                        "current": {"fitness": days[-1]["fitness"], "form": days[-1]["form"], "status": status}}}


# ---- off by default, and nothing without a reason

def test_it_is_off_until_you_choose_a_tone():
    assert coach.tick(summary(idle=6), NOW, {**prefs.DEFAULTS}) == (None, [])


def test_a_reminder_after_a_few_days_says_why_from_your_data():
    card, events = coach.tick(summary(idle=4), NOW, settings())
    assert card["id"] == "idle" and card["reason"] == "4 days since your last activity"
    assert len(events) == 1 and "4 days since your last activity." in events[0]["body"] and events[0]["title"] == "A gentle nudge"


def test_no_reminder_when_you_were_active_recently():
    card, events = coach.tick(summary(idle=1), NOW, settings(), chance=1.0)
    assert (card is None or card["id"] != "idle") and events == []


def test_a_falling_fitness_is_a_reason_even_when_you_rode_yesterday():
    slide = series(fitness=[40.0] * 46 + [40 - i for i in range(14)], form=0, load=20)
    card, events = coach.tick(summary(idle=1, days=slide), NOW, settings())
    assert card["id"] == "falling" and "fitness is down" in card["reason"] and "two weeks" in card["reason"]
    assert len(events) == 1


def test_a_flat_fitness_gives_no_falling_nudge():
    assert coach.facts(summary(days=series(fitness=30)), NOW)["change_14d"] == 0


def test_the_first_weeks_of_history_are_not_trusted_for_trends():
    slide = series(fitness=[40.0] * 46 + [40 - i for i in range(14)])
    assert coach.facts(summary(days=slide, warming=True), NOW)["change_14d"] is None


# ---- sense: recovery, pause, quiet hours, caps

def test_a_recovery_week_after_a_hard_block_gets_a_kind_note_and_no_reminders():
    dipped = series(form=[0.0] * 50 + [-14.0, -12.0, -8.0, -5.0, -3.0, -1.0, 0, 0, 0, 0])
    card, events = coach.tick(summary(idle=5, days=dipped), NOW, settings(coach_tone="drill"), chance=1.0)
    assert card["id"] == "recovery" and "dipped to -14" in card["reason"] and card["notify"] is False
    assert events == []


def test_a_hard_week_followed_by_an_easy_one_counts_as_recovery():
    loads = [50.0] * 28 + [50.0] * 7 + [120.0] * 7 + [40.0] * 7 + [10.0] * 7 + [5.0]      # the week before last was the big one
    hard = series(n=50, load=loads[:50])
    facts = coach.facts(summary(idle=4, days=hard), NOW)
    assert facts["recovery"] == "last week was one of your hardest in a while"


def test_only_two_tones_exist_besides_silent():
    assert coach.MODES == ("off", "motivational", "drill") and coach.TONES == ("motivational", "drill")
    assert set(coach.messages()["titles"]) == {"motivational", "drill"}


def test_a_pause_silences_everything_until_its_last_day():
    paused = settings(coach_pause_until="2026-09-27")
    assert coach.tick(summary(idle=8), NOW, paused, chance=1.0) == (None, [])
    assert coach.paused(paused, datetime(2026, 9, 27, 23, 0)) and not coach.paused(paused, datetime(2026, 9, 28, 8, 0))
    assert coach.tick(summary(idle=8), datetime(2026, 9, 28, 12, 0), paused)[1]                                   # it resumes


def test_quiet_hours_and_the_hour_after_an_activity_keep_notifications_back_but_not_the_card():
    night = datetime(2026, 9, 20, 23, 30)
    card, events = coach.tick(summary(idle=5), night, settings())
    assert card["id"] == "idle" and events == []
    early = datetime(2026, 9, 21, 7, 59)
    assert coach.tick(summary(idle=5), early, settings())[1] == []
    fresh = summary(idle=0, hours_ago=0.5)
    assert coach.tick(fresh, NOW, settings(), chance=1.0)[1] == []


def test_the_quiet_window_crosses_midnight_correctly():
    assert coach._quiet(datetime(2026, 1, 1, 22, 0), "22:00-08:00") and coach._quiet(datetime(2026, 1, 1, 3, 0), "22:00-08:00")
    assert not coach._quiet(datetime(2026, 1, 1, 8, 0), "22:00-08:00") and not coach._quiet(datetime(2026, 1, 1, 12, 0), "22:00-08:00")
    assert coach._quiet(datetime(2026, 1, 1, 10, 0), "09:00-17:00") and not coach._quiet(datetime(2026, 1, 1, 18, 0), "09:00-17:00")


def test_one_data_reminder_a_day_and_random_pushes_only_with_a_reason_and_within_the_cap():
    s = summary(idle=4)
    assert len(coach.tick(s, NOW, settings(), chance=1.0)[1]) == 1
    later = NOW + timedelta(hours=2)
    second = coach.tick(s, later, settings(coach_random_per_day=2), chance=1.0)[1]
    assert len(second) == 1 and second[0]["kind"] == "random" and "4 days since your last activity." in second[0]["body"]
    third = coach.tick(s, later + timedelta(hours=2), settings(coach_random_per_day=2), chance=1.0)[1]
    assert len(third) == 1
    assert coach.tick(s, later + timedelta(hours=4), settings(coach_random_per_day=2), chance=1.0)[1] == []      # cap reached


def test_random_pushes_never_come_without_a_reason_in_the_data():
    assert coach.tick(summary(idle=2), NOW, settings(), chance=1.0)[1] == []
    assert coach.tick(summary(idle=0), NOW, settings(), chance=1.0)[1] == []


def test_a_long_break_gets_one_kind_message_every_few_days_not_a_daily_one():
    assert len(coach.tick(summary(idle=15), NOW, settings())[1]) == 1
    assert coach.tick(summary(idle=16), NOW + timedelta(days=1, hours=1), settings(), chance=0)[1] == []


def test_a_line_is_not_repeated_right_after_it_was_sent():
    said = []
    for day in range(6):
        events = coach.tick(summary(idle=4), NOW + timedelta(days=day), settings())[1]
        said.append(events[0]["body"].split("\n")[0])
    assert len(set(said)) >= 4


def test_good_news_and_rest_advice_are_popups_too_but_only_the_ones_that_ask_you_to_move_carry_buttons():
    good = coach.tick(summary(idle=1, status="fresh"), NOW, settings())
    assert good[0]["id"] == "fresh" and good[1][0]["actions"]
    coach._save_state({})                                                     # a new day for the next case
    praise = coach.tick(summary(idle=0, change28=20), NOW, settings())
    assert praise[0]["id"] == "praise" and "actions" not in praise[1][0]
    coach._save_state({})
    rest = coach.tick(summary(idle=2, status="overreaching"), NOW, settings())
    assert rest[0]["id"] == "rest" and "actions" not in rest[1][0]


def test_a_reminder_carries_three_excuse_buttons_with_readable_labels():
    _, events = coach.tick(summary(idle=4), NOW, settings())
    assert [(a["key"], a["label"]) for a in events[0]["actions"]] == [("tired", "I'm tired"), ("weather", "Bad weather"), ("time", "No time")]


# ---- excuses

def test_an_excuse_for_today_silences_the_nudges_and_the_card_says_it_was_noted():
    card, events = coach.tick(summary(idle=6), NOW, settings(coach_tone="drill"), chance=1.0, marked={NOW.date().isoformat(): "tired"})
    assert card["id"] == "excused" and "I'm tired" in card["text"] and events == []


def test_feeling_tired_or_unwell_also_quiets_the_next_day_but_the_weather_does_not():
    yesterday = (NOW - timedelta(days=1)).date().isoformat()
    for key in ("tired", "unwell"):
        card, events = coach.tick(summary(idle=6), NOW, settings(), chance=1.0, marked={yesterday: key})
        assert card["id"] == "excused" and events == [], key
    card, events = coach.tick(summary(idle=6), NOW, settings(), marked={yesterday: "weather"})
    assert card["id"] == "idle" and len(events) == 1
    day_before = (NOW - timedelta(days=2)).date().isoformat()
    assert coach.tick(summary(idle=6), NOW, settings(), marked={day_before: "tired"})[0]["id"] == "idle"


def test_excuses_are_stored_toggled_and_labelled(tmp_path):
    assert excuses.load() == {}
    assert excuses.toggle("2026-09-20", "tired") == {"2026-09-20": "tired"}
    assert excuses.toggle("2026-09-20", "weather") == {"2026-09-20": "weather"}          # a different one replaces it
    assert excuses.toggle("2026-09-20", "weather") == {}                                   # the same one takes it back
    assert excuses.add("2026-09-21", "time") == {"2026-09-21": "time"} and excuses.add("2026-09-21", "time") == {"2026-09-21": "time"}
    assert excuses.clear("2026-09-21") == {}
    with pytest.raises(ValueError):
        excuses.toggle("2026-09-20", "hangover")
    with pytest.raises(ValueError):
        excuses.toggle("not-a-day", "tired")
    excuses.toggle("2026-09-20", "unwell")
    assert stat.S_IMODE(excuses._file().stat().st_mode) == 0o600


def test_the_excuse_command_marks_and_clears_days(capsys):
    assert json.loads(run("excuse", "tired", "--date", "2026-09-19", capsys=capsys)[1])["excuses"] == {"2026-09-19": "tired"}
    assert json.loads(run("excuse", "clear", "--date", "2026-09-19", capsys=capsys)[1])["excuses"] == {}
    today = datetime.now().date().isoformat()
    assert json.loads(run("excuse", "rest", capsys=capsys)[1])["excuses"] == {today: "rest"}
    code, out = run("excuse", "tired", "--date", "yesterday", capsys=capsys)
    assert code == 1 and json.loads(out)["error"] == "bad_value"


# ---- the popup itself

def test_a_button_press_on_the_popup_records_that_days_excuse(monkeypatch):
    seen = []
    def fake_run(cmd, **kw):
        seen.append(cmd)
        class Done:
            stdout = "tired\n"
        return Done()
    monkeypatch.setattr(coach.subprocess, "run", fake_run)
    event = coach.notification("idle", "drill", "Up.", "4 days since your last activity")
    assert coach.show(event, "2026-09-20") == "tired" and excuses.load() == {"2026-09-20": "tired"}
    assert seen[0][:3] == ["notify-send", "-a", "lapbar"] and "tired=I'm tired" in seen[0] and "weather=Bad weather" in seen[0]


def test_dismissing_the_popup_records_nothing(monkeypatch):
    class Done:
        stdout = ""
    monkeypatch.setattr(coach.subprocess, "run", lambda cmd, **kw: Done())
    assert coach.show(coach.notification("idle", "drill", "Up."), "2026-09-20") is None and excuses.load() == {}


def test_delivering_a_popup_starts_a_detached_process_and_a_refresh_does_it_for_each_nudge(monkeypatch, capsys):
    started = []
    monkeypatch.setattr(coach.subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw)))
    coach.deliver(coach.notification("idle", "drill", "Up."), "2026-09-20")
    cmd, kw = started[0]
    assert cmd[-2] == "--deliver" and json.loads(cmd[-1])["day"] == "2026-09-20" and kw["start_new_session"] is True


def test_the_deliver_command_shows_the_popup_it_is_given(monkeypatch, capsys):
    shown = []
    monkeypatch.setattr(coach, "show", lambda event, day, timeout_ms=0: shown.append((event["title"], day)))
    payload = json.dumps({"event": coach.notification("idle", "drill", "Up."), "day": "2026-09-20"})
    assert run("coach", "--deliver", payload, capsys=capsys)[0] == 0 and shown == [("ATTENTION", "2026-09-20")]


# ---- the words

BANNED = ("fat", "lazy", "weight", "ugly", "pathetic", "loser", "worthless", "stupid", "disgusting", "hitler", "nazi")


def all_lines():
    for kind, tones in coach.messages().items():
        if kind != "titles":
            for tone, lines in tones.items():
                for line in lines:
                    yield kind, tone, line


def test_every_kind_has_lines_in_every_tone_and_they_all_format():
    messages = coach.messages()
    for kind in ("idle", "falling", "comeback", "rest", "fresh", "praise", "recovery", "excused", "random"):
        assert set(messages[kind]) == set(coach.TONES), kind
    for kind, tone, line in all_lines():
        assert line.format(days=4, drop=18, excuse="I'm tired")
    assert set(messages["titles"]) == set(coach.TONES)


def test_the_words_never_target_a_body_or_a_person_and_the_sergeant_is_after_the_chair():
    for kind, tone, line in all_lines():
        words = set(re.findall(r"[a-z']+", line.lower()))
        assert not words & set(BANNED), (kind, tone, line)
    drill = " ".join(l for k, t, l in all_lines() if t == "drill").lower()
    assert "kudos" in drill and "chair" in drill and "couch" in drill                     # the jokes are about these
    assert any("kudos" in l.lower() for k, t, l in all_lines() if t == "drill" and k in ("idle", "random"))


def test_the_motivational_tone_is_mellow_and_suggests_gentle_activities():
    calm = [l for k, t, l in all_lines() if t == "motivational"]
    assert calm and not any(re.search(r"\b[A-Z]{3,}\b", l) for l in calm)                # no shouting
    idle = " ".join(coach.messages()["idle"]["motivational"]).lower()
    assert all(word in idle for word in ("walk", "easy", "spin", "stretch", "swim"))
    assert any(re.search(r"\b[A-Z]{3,}\b", l) for k, t, l in all_lines() if t == "drill")     # the sergeant does shout


# ---- preferences and commands

def test_tone_random_pushes_quiet_hours_and_pause_are_validated_and_saved():
    assert prefs.update(coach_tone="drill", coach_random_per_day=4, quiet_hours="21:30-07:00")["coach_tone"] == "drill"
    for bad in ({"coach_tone": "cheeky"}, {"coach_tone": "furious"}, {"coach_random_per_day": 40}, {"quiet_hours": "late"}, {"quiet_hours": "22:00"}):
        with pytest.raises(ValueError):
            prefs.update(**bad)


def run(*argv, capsys):
    with pytest.raises(SystemExit) as done:
        cli.main(list(argv))
    return done.value.code, capsys.readouterr().out


def test_the_prefs_command_sets_the_tone_and_a_pause_in_days(capsys):
    code, out = run("prefs", "--coach-tone", "drill", "--coach-pause", "7", capsys=capsys)
    data = json.loads(out)["prefs"]
    assert code == 0 and data["coach_tone"] == "drill"
    assert data["coach_pause_until"] == (datetime.now() + timedelta(days=7)).date().isoformat()
    assert json.loads(run("prefs", "--coach-pause", "0", capsys=capsys)[1])["prefs"]["coach_pause_until"] is None


def test_the_test_command_shows_a_sample_of_a_tone_without_buttons_so_trying_it_records_nothing(monkeypatch, capsys):
    shown = []
    monkeypatch.setattr(coach, "show", lambda event, day, timeout_ms=0: shown.append(event))
    code, out = run("coach", "--test", "--tone", "drill", capsys=capsys)
    event = json.loads(out)
    assert code == 0 and event["tone"] == "drill" and event["title"] == "ATTENTION" and "actions" not in event
    assert shown[0]["body"].endswith("Example only: this is how a nudge with its data reason looks.")


def test_a_refresh_shows_the_popups_and_caches_only_the_card(monkeypatch, capsys):
    from lapbar.providers import strava
    prefs.update(coach_tone="drill")
    fake = {"provider": "strava", "latest": {"start": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}, "days": {}}
    monkeypatch.setattr(strava, "fetch", lambda **kw: dict(fake))
    monkeypatch.setattr(coach, "_quiet", lambda now, hours: False)
    delivered = []
    monkeypatch.setattr(coach, "deliver", lambda event, day=None: delivered.append(event))
    code, out = run("fetch", "--print", capsys=capsys)
    result = json.loads(out)
    assert code == 0 and result["coach"]["id"] == "idle" and "coach_events" not in result
    assert len(delivered) == 1 and delivered[0]["title"] == "ATTENTION" and delivered[0]["actions"]
    cached = json.loads(cli.config.cache_path().read_text())
    assert cached["coach"]["id"] == "idle"


def test_the_coach_command_gives_the_popup_the_tone_the_card_and_the_excuses(capsys):
    prefs.update(coach_tone="motivational")
    excuses.toggle("2026-09-20", "tired")
    info = json.loads(run("coach", capsys=capsys)[1])
    assert info["tone"] == "motivational" and info["excuses"] == {"2026-09-20": "tired"} and info["excuse_labels"]["tired"] == "I'm tired"
    assert info["paused"] is False
