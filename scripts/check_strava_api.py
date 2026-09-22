#!/usr/bin/env python3
"""Compare LapBar's Strava API calls with the API's published spec (needs a network connection).

    python scripts/check_strava_api.py

Exit status 0: every call LapBar makes is still in the spec, not deprecated, and has the parameters it uses.
Exit status 1: something changed; the report says what. A monthly GitHub workflow runs this, so a change in
Strava's API shows up as a failed run instead of a surprise. Also prints a reminder when the announced base URL
change (see lapbar/stravaapi.py) is near.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lapbar import stravaapi  # noqa: E402
from lapbar.http import request_json  # noqa: E402

SPEC_MAX_BYTES = 8 * 1024 * 1024   # Strava's swagger.json is well under 1 MiB today; generous room to grow


def main() -> int:
    spec = request_json(stravaapi.SPEC_URL, max_bytes=SPEC_MAX_BYTES)
    problems, notes = stravaapi.check_spec(spec)
    print(f"Strava API {stravaapi.API_VERSION}, spec {(spec.get('info') or {}).get('version')} "
          f"(LapBar checked against {stravaapi.API_SPEC_VERSION} on {stravaapi.SPEC_CHECKED_ON})")
    for note in notes:
        print(f"  note: {note}")
    move = stravaapi.BASE_URL_MIGRATION
    days_left = (date.fromisoformat(move["available_from"]) - date.today()).days
    if days_left <= 90:
        print(f"  REMINDER: Strava's new base URL {move['new_base']} is available from {move['available_from']} "
              f"({days_left} days). Check {stravaapi.CHANGELOG_URL} and update lapbar/stravaapi.py.")
    for problem in problems:
        print(f"  PROBLEM: {problem}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
