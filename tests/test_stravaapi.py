"""The Strava API version LapBar targets and every call it makes: one registry, kept in step with code and README."""
import json
import re
from pathlib import Path

import pytest

from lapbar import auth, cli, comments, details, kudos, raw, setup, stravaapi
from lapbar.providers import strava

ROOT = Path(__file__).resolve().parent.parent


def test_the_registry_says_which_api_version_and_where_it_lives():
    assert stravaapi.API_VERSION == "v3" and stravaapi.BASE == "https://www.strava.com/api/v3"
    assert re.fullmatch(r"\d+\.\d+\.\d+", stravaapi.API_SPEC_VERSION) and stravaapi.SPEC_CHECKED_ON.startswith("20")
    assert stravaapi.BASE_URL_MIGRATION["new_base"] == "https://api-v3.strava.com"


def test_lapbar_only_reads_from_the_api():
    api_methods = {c["method"] for c in stravaapi.CALLS if c["base"] == stravaapi.BASE}
    assert api_methods == {"GET"}                                   # the only POST is the OAuth token exchange
    assert [c["id"] for c in stravaapi.CALLS if c["method"] == "POST"] == ["oauth_token"]


@pytest.mark.parametrize("address, call_id", [
    (strava.ACTIVITIES_URL, "athlete_activities"),
    (details.DETAIL_URL.format(id=1), "activity_detail"),
    (raw.URL.format(id=1), "activity_streams"),
    (kudos.KUDOS_URL.format(id=1), "activity_kudos"),
    (comments.COMMENTS_URL.format(id=1), "activity_comments"),
    (comments.ATHLETE_URL, "athlete"),
    (setup.ATHLETE_URL, "athlete"),
    (auth.TOKEN_URL, "oauth_token"),
    (auth.AUTHORIZE_URL, "oauth_authorize"),
])
def test_every_address_the_code_uses_is_a_registered_call(address, call_id):
    assert stravaapi.call_for(address)["id"] == call_id


def test_addresses_are_matched_by_path_not_by_prefix():
    assert stravaapi.call_for("https://www.strava.com/api/v3/activities/5/streams?keys=time")["id"] == "activity_streams"
    assert stravaapi.call_for("https://www.strava.com/api/v3/activities/5")["id"] == "activity_detail"
    assert stravaapi.call_for("https://www.strava.com/api/v3/activities/5/comments")["id"] == "activity_comments"
    assert stravaapi.call_for("https://www.strava.com/api/v3/activities/5/laps") is None                # not one LapBar makes
    assert stravaapi.call_for("https://www.strava.com/api/v3/activities/5/streams/extra") is None


def test_no_module_hardcodes_a_strava_api_address():
    offenders = []
    for path in (ROOT / "lapbar").rglob("*.py"):
        if path.name != "stravaapi.py" and re.search(r"strava\.com/(api|oauth)", path.read_text()):
            offenders.append(path.name)
    assert offenders == [], f"use lapbar.stravaapi instead: {offenders}"


def test_the_cli_fallback_request_goes_through_the_registry(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_read_cache", lambda: None)
    monkeypatch.setattr(cli.history, "find", lambda i: None)
    monkeypatch.setattr(cli, "request_json", lambda url, token=None, **kw: seen.update(url=url) or {"id": 7, "start_date_local": "2025-01-01T00:00:00Z"})
    cli._activity_for(7, "tok")
    assert seen["url"] == "https://www.strava.com/api/v3/activities/7"


# ---- comparing with Strava's published spec

def fake_spec():
    spec = {"info": {"version": stravaapi.API_SPEC_VERSION}, "host": "www.strava.com", "basePath": "/api/v3",
            "parameters": {"page": {"name": "page"}, "perPage": {"name": "per_page"}}, "paths": {}}
    for c in stravaapi.CALLS:
        if c["spec"]:
            params = [{"name": p} for p in c["params"] if p not in ("per_page", "page")]
            params += [{"$ref": "#/parameters/perPage"}] if "per_page" in c["params"] else []
            params += [{"$ref": "#/parameters/page"}] if "page" in c["params"] else []
            spec["paths"][c["path"]] = {c["method"].lower(): {"parameters": params}}
    return spec


def test_a_matching_spec_has_no_problems_and_resolves_shared_parameters():
    problems, notes = stravaapi.check_spec(fake_spec())
    assert problems == [] and "read-only" in notes[0]


def test_a_changed_spec_is_reported_in_plain_words():
    spec = fake_spec()
    spec["info"]["version"] = "4.0.0"
    spec["basePath"] = "/v4"
    del spec["paths"]["/athlete"]
    spec["paths"]["/athlete/activities"]["get"]["deprecated"] = True
    spec["paths"]["/activities/{id}/streams"]["get"]["parameters"] = [{"name": "keys"}]
    problems = "\n".join(stravaapi.check_spec(spec)[0])
    for expected in ("'4.0.0'", "/v4", "GET /athlete is not in the spec", "GET /athlete/activities is marked deprecated",
                     "'key_by_type' is not in the spec"):
        assert expected in problems


def test_the_committed_script_and_monthly_workflow_exist():
    script = (ROOT / "scripts" / "check_strava_api.py").read_text()
    assert "check_spec" in script and "BASE_URL_MIGRATION" in script
    workflow = (ROOT / ".github" / "workflows" / "strava-api.yml").read_text()
    assert "schedule:" in workflow and "workflow_dispatch" in workflow and "check_strava_api.py" in workflow


# ---- the command and the README

def test_the_api_command_prints_the_version_and_every_call(capsys):
    with pytest.raises(SystemExit):
        cli.main(["api"])
    text = capsys.readouterr().out
    assert "Strava API v3" in text and "2027-01-04" in text
    assert all(c["path"] in text for c in stravaapi.CALLS)
    with pytest.raises(SystemExit):
        cli.main(["api", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["api"] == "v3" and data["base"] == stravaapi.BASE and len(data["calls"]) == len(stravaapi.CALLS)


def test_the_readme_lists_every_call_and_the_version():
    readme = (ROOT / "README.md").read_text()
    assert f"Strava API {stravaapi.API_VERSION}" in readme and stravaapi.API_SPEC_VERSION in readme
    for c in stravaapi.CALLS:
        assert f"`{(c['base'] + c['path']).replace('https://www.strava.com', '')}`" in readme, c["id"]
    assert stravaapi.BASE_URL_MIGRATION["new_base"] in readme and stravaapi.BASE_URL_MIGRATION["available_from"] in readme


# ---- the granted-scope check tolerates a format change

@pytest.mark.parametrize("text", ["read,activity:read_all", "read activity:read_all", "read, activity:read_all", "activity:read_all"])
def test_granted_scopes_are_read_from_commas_or_spaces(text):
    assert "activity:read_all" in auth.granted_scopes(text)


def test_missing_or_empty_scopes_do_not_pass():
    assert "activity:read_all" not in auth.granted_scopes("read,activity:read")
    assert auth.granted_scopes("") == set() and auth.granted_scopes(None) == set()
