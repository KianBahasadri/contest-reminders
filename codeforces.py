import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from linear_sync import (
    Source,
    TARGET_TIMEZONE,
    USER_AGENT,
    format_date,
    format_time,
)


CODEFORCES_CONTESTS_URL = "https://codeforces.com/api/contest.list?gym=false"
USER_RATING = 393
REQUEST_TIMEOUT_SECONDS = 20
FETCH_ATTEMPTS = 3
FETCH_RETRY_DELAY_SECONDS = 2


def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
    last_error: Exception | None = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                return json.load(response)
        except json.JSONDecodeError as exc:
            last_error = exc
            message = "Codeforces contests API returned invalid JSON"
        except urllib.error.HTTPError as exc:
            last_error = exc
            message = f"Codeforces contests API request failed with HTTP {exc.code}"
            if exc.code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(message) from exc
        except urllib.error.URLError as exc:
            last_error = exc
            message = f"Codeforces contests API request failed: {exc.reason}"
        except TimeoutError as exc:
            last_error = exc
            message = "Codeforces contests API request timed out"

        if attempt < FETCH_ATTEMPTS:
            time.sleep(FETCH_RETRY_DELAY_SECONDS)
            continue

        raise RuntimeError(message) from last_error

    raise RuntimeError("Codeforces contests API request failed")


def extract_divisions(name: str) -> set[int]:
    normalized = name.lower().replace("division", "div")
    matches = re.findall(r"div\.?\s*(\d)", normalized)
    return {int(match) for match in matches}


def allowed_divisions_for_rating(rating: int) -> set[int]:
    if rating >= 2100:
        return {1}
    if rating >= 1900:
        return {1, 2}
    if rating >= 1600:
        return {2}
    if rating >= 1400:
        return {2, 3}
    if rating >= 0:
        return {2, 3, 4}
    return set()


def format_division_short(divisions: set[int]) -> str:
    return "D" + "+".join(str(division) for division in sorted(divisions))


def build_round_label(name: str) -> str:
    match = re.search(r"Educational Codeforces Round \d+", name)
    if match:
        return match.group(0)

    match = re.search(r"Codeforces Round (\d+)", name)
    if match:
        return f"Codeforces {match.group(1)}"

    match = re.search(r"Codeforces Round(?: \d+)?(?: \(Div\.[^)]+\))?", name)
    if match:
        return match.group(0)

    return name


def start_time(contest: dict[str, object]) -> datetime:
    return datetime.fromtimestamp(
        int(contest["startTimeSeconds"]), tz=TARGET_TIMEZONE
    )


def build_issue_title(contest: dict[str, object], start_time: datetime) -> str:
    divisions = extract_divisions(str(contest["name"]))
    contest_name = str(contest["name"])
    round_label = build_round_label(contest_name)
    if round_label == contest_name and not re.search(r"Round \d+", contest_name):
        round_label = f"Codeforces #{contest['id']}"
    return (
        f"{round_label} {format_division_short(divisions)} - {format_time(start_time)}"
    )


def build_issue_description(contest: dict[str, object], start_time: datetime) -> str:
    duration_seconds = int(contest.get("durationSeconds", 0) or 0)
    hours = duration_seconds // 3600
    minutes = (duration_seconds % 3600) // 60
    duration = f"{hours}h" + (f" {minutes}m" if minutes else "")
    return "\n".join(
        [
            f"{contest['name']} starts on {format_date(start_time)} at {format_time(start_time)}.",
            f"Duration: {duration}",
            f"Type: {contest.get('type', 'unknown')}",
            f"URL: https://codeforces.com/contest/{contest['id']}",
        ]
    )


def contest_reason(contest: dict[str, object]) -> tuple[bool, str]:
    allowed_divisions = allowed_divisions_for_rating(USER_RATING)
    divisions = extract_divisions(str(contest["name"]))
    if not divisions:
        return False, "skipped: no supported division marker in contest name"

    eligible_divisions = divisions & allowed_divisions
    if eligible_divisions:
        return (
            True,
            f"eligible: rating {USER_RATING} qualifies for {format_division_short(eligible_divisions)}",
        )
    return (
        False,
        f"skipped: rating {USER_RATING} does not qualify for {format_division_short(divisions)}",
    )


def prefix(contest: dict[str, object], start_time: datetime) -> str:
    return f"{format_date(start_time)}: {contest['name']}"


def fetch_contests() -> list[dict[str, object]]:
    payload = fetch_json(CODEFORCES_CONTESTS_URL, headers={"User-Agent": USER_AGENT})
    if not isinstance(payload, dict) or payload.get("status") != "OK":
        raise RuntimeError("Unexpected Codeforces response")

    contests = payload.get("result")
    if not isinstance(contests, list):
        raise RuntimeError("Codeforces returned no contests")

    now = datetime.now(timezone.utc).timestamp()
    upcoming = [
        contest
        for contest in contests
        if isinstance(contest, dict)
        and contest.get("phase") == "BEFORE"
        and float(contest.get("startTimeSeconds", 0) or 0) > now
    ]
    upcoming.sort(key=lambda contest: int(contest["startTimeSeconds"]))
    return upcoming


SOURCE = Source(
    name="codeforces",
    empty_message=None,
    fetch_items=fetch_contests,
    start_time=start_time,
    title=build_issue_title,
    description=build_issue_description,
    prefix=prefix,
    eligibility=contest_reason,
    fetch_error_prefix="Unable to fetch Codeforces contests",
)


def main() -> None:
    from main import run

    raise SystemExit(run(["codeforces"]))


if __name__ == "__main__":
    main()
