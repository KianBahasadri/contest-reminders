import json
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


DMOJ_CONTESTS_URL = "https://dmoj.ca/api/v2/contests"


def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        mitigation = exc.headers.get("cf-mitigated") if exc.headers else None
        if exc.code == 403 and mitigation == "challenge":
            raise RuntimeError(
                "DMOJ contests API is blocked by a Cloudflare challenge"
            ) from exc
        raise RuntimeError(
            f"DMOJ contests API request failed with HTTP {exc.code}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DMOJ contests API request failed: {exc.reason}") from exc


def parse_contest_time(value: object) -> datetime:
    return datetime.fromisoformat(str(value)).astimezone(TARGET_TIMEZONE)


def start_time(contest: dict[str, object]) -> datetime:
    return parse_contest_time(contest["start_time"])


def latest_start_time(contest: dict[str, object]) -> datetime:
    contest_start = start_time(contest)
    time_limit_seconds = contest.get("time_limit")
    if not time_limit_seconds:
        return contest_start

    end_time = parse_contest_time(contest["end_time"])
    return datetime.fromtimestamp(
        end_time.timestamp() - float(time_limit_seconds), tz=TARGET_TIMEZONE
    )


def due_time(contest: dict[str, object], start_time: datetime) -> datetime:
    return latest_start_time(contest)


def build_issue_title(contest: dict[str, object], start_time: datetime) -> str:
    return f"DMOJ {contest['name']} - {format_time(start_time)}"


def build_issue_description(contest: dict[str, object], start_time: datetime) -> str:
    end_time = parse_contest_time(contest["end_time"])
    duration = end_time - start_time
    total_minutes = int(duration.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    duration_text = f"{hours}h" + (f" {minutes}m" if minutes else "")
    rated_text = "Yes" if contest.get("is_rated") else "No"
    tags = contest.get("tags") or []
    tags_text = ", ".join(str(tag) for tag in tags) if tags else "None"
    latest_start = latest_start_time(contest)
    return "\n".join(
        [
            f"{contest['name']} starts on {format_date(start_time)} at {format_time(start_time)}.",
            f"Duration: {duration_text}",
            (
                f"Latest start: {format_date(latest_start)} at {format_time(latest_start)}"
                if contest.get("time_limit")
                else "Latest start: contest start time"
            ),
            f"Rated: {rated_text}",
            f"Tags: {tags_text}",
            f"URL: https://dmoj.ca/contest/{contest['key']}",
        ]
    )


def prefix(contest: dict[str, object], start_time: datetime) -> str:
    return f"{format_date(start_time)}: {contest['name']}"


def fetch_contests() -> list[dict[str, object]]:
    payload = fetch_json(DMOJ_CONTESTS_URL, headers={"User-Agent": USER_AGENT})
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected DMOJ response")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("DMOJ returned no data")

    contests = data.get("objects")
    if not isinstance(contests, list):
        raise RuntimeError("DMOJ returned no contests")

    now = datetime.now(TARGET_TIMEZONE)
    upcoming = [
        contest
        for contest in contests
        if isinstance(contest, dict)
        and contest.get("start_time")
        and latest_start_time(contest) > now
    ]
    upcoming.sort(key=latest_start_time)
    return upcoming


SOURCE = Source(
    name="dmoj",
    empty_message="No startable DMOJ contests found.",
    fetch_items=fetch_contests,
    start_time=start_time,
    title=build_issue_title,
    description=build_issue_description,
    prefix=prefix,
    due_time=due_time,
    fetch_error_prefix="Unable to fetch DMOJ contests",
)


def main() -> None:
    from main import run

    raise SystemExit(run(["dmoj"]))


if __name__ == "__main__":
    main()
