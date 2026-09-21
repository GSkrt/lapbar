import pytest

from lapbar import kudos
from lapbar.http import HttpError


def acts(**counts):
    """Newest first. acts(a=3, b=0) -> two activities with those kudos counts."""
    return [{"id": i + 1, "name": name, "kudos": n} for i, (name, n) in enumerate(counts.items())]


def people(*names):
    return [{"firstname": n.split()[0], "lastname": n.split()[1]} for n in names]


@pytest.fixture
def api(monkeypatch):
    """Fake kudos endpoint: api.by_activity[id] = names; api.calls records requests."""
    class Api:
        by_activity: dict = {}
        calls: list = []
        fail: Exception | None = None
    a = Api()
    a.by_activity, a.calls = {}, []

    def fake(url, token=None, **kw):
        act_id = int(url.split("/activities/")[1].split("/")[0])
        a.calls.append(act_id)
        if a.fail:
            raise a.fail
        return people(*a.by_activity.get(act_id, []))

    monkeypatch.setattr(kudos, "request_json", fake)
    return a


def test_first_run_is_a_baseline_and_seeds_names_without_events(api):
    api.by_activity = {1: ["Ana K.", "Bo M."]}
    events, seen, names = kudos.track("t", acts(ride=2, walk=0), None)
    assert events == []
    assert seen == {"1": 2, "2": 0}
    assert names == {"1": ["Ana K.", "Bo M."]}
    assert api.calls == [1]  # nothing to look up for the activity without kudos


def test_new_kudos_are_attributed_to_the_new_people_only(api):
    api.by_activity = {1: ["Ana K.", "Bo M.", "Cy D."]}
    prev = {"kudos_seen": {"1": 2}, "kudoers": {"1": ["Ana K.", "Bo M."]}}
    events, seen, names = kudos.track("t", acts(ride=3), prev)
    assert events == [{"activity_id": 1, "name": "ride", "url": None, "count": 1, "total": 3, "from": ["Cy D."]}]
    assert seen == {"1": 3} and names["1"] == ["Ana K.", "Bo M.", "Cy D."]


def test_no_change_means_no_request_and_no_event(api):
    prev = {"kudos_seen": {"1": 2}, "kudoers": {"1": ["Ana K.", "Bo M."]}}
    events, seen, _ = kudos.track("t", acts(ride=2), prev)
    assert events == [] and api.calls == [] and seen == {"1": 2}


def test_increase_without_known_names_reports_count_only(api):
    api.by_activity = {1: ["Ana K.", "Bo M."]}
    events, _, names = kudos.track("t", acts(ride=2), {"kudos_seen": {"1": 1}})
    assert events[0]["count"] == 1 and events[0]["from"] == []
    assert names["1"] == ["Ana K.", "Bo M."]  # so the next one can be attributed


def test_failed_lookup_is_retried_next_time(api):
    api.fail = HttpError(429, "slow down")
    prev = {"kudos_seen": {"1": 1}, "kudoers": {"1": ["Ana K."]}}
    events, seen, _ = kudos.track("t", acts(ride=2), prev)
    assert events == [] and seen == {"1": 1}          # not advanced
    api.fail, api.by_activity = None, {1: ["Ana K.", "Bo M."]}
    events, seen, _ = kudos.track("t", acts(ride=2), {"kudos_seen": seen, "kudoers": {"1": ["Ana K."]}})
    assert events[0]["from"] == ["Bo M."] and seen == {"1": 2}


def test_one_failure_stops_further_requests_this_round(api):
    api.fail = HttpError(429, "slow down")
    kudos.track("t", acts(a=2, b=2, c=2), {"kudos_seen": {"1": 0, "2": 0, "3": 0}})
    assert len(api.calls) == 1


def test_withdrawn_kudos_lower_the_baseline_without_event(api):
    events, seen, _ = kudos.track("t", acts(ride=1), {"kudos_seen": {"1": 3}, "kudoers": {"1": ["A B."]}})
    assert events == [] and seen == {"1": 1}


def test_older_cache_without_tracking_uses_its_activity_list_as_baseline(api):
    api.by_activity = {1: ["Ana K.", "Bo M."]}
    prev = {"activities": [{"id": 1, "kudos": 1}]}
    events, _, _ = kudos.track("t", acts(ride=2), prev)
    assert events[0]["count"] == 1


def test_brand_new_activity_with_kudos_counts_from_zero(api):
    api.by_activity = {2: ["Ana K."]}
    events, _, _ = kudos.track("t", acts(new=1, old=0), {"kudos_seen": {"1": 0}})
    assert events[0]["name"] == "new" and events[0]["count"] == 1


def test_name_seeding_is_bounded(api):
    many = acts(**{f"a{i}": 1 for i in range(8)})
    kudos.track("t", many, None)
    assert len(api.calls) == kudos.SEED_LIMIT


def test_only_recent_activities_are_watched(api):
    many = acts(**{f"a{i}": 0 for i in range(kudos.RECENT + 5)})
    _, seen, _ = kudos.track("t", many, None)
    assert len(seen) == kudos.RECENT


def test_widening_the_watch_does_not_announce_old_kudos_as_new(api):
    """An activity that was not watched before starts from the count the last list showed."""
    previous = {"kudos_seen": {"1": 3}, "kudoers": {}, "activities": [{"id": 1, "kudos": 3}, {"id": 2, "kudos": 8}]}
    listed = [{"id": 1, "name": "a", "kudos": 3}, {"id": 2, "name": "b", "kudos": 8}]
    events, seen, _ = kudos.track("t", listed, previous, seed_limit=0)
    assert events == [] and seen == {"1": 3, "2": 8}
    listed[1]["kudos"] = 9                                       # a real new kudo on the newly watched activity
    previous["kudoers"] = {"2": ["Ana K."]}
    api.by_activity[2] = ["Ana K.", "Xen Y."]
    events, _, _ = kudos.track("t", listed, previous, seed_limit=0)
    assert [(e["count"], e["from"]) for e in events] == [(1, ["Xen Y."])]
