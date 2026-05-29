import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from linear_sync import (
    Source,
    TARGET_TIMEZONE,
    USER_AGENT,
    format_date,
    format_time,
    require_env,
)


CLIST_CONTESTS_URL = "https://clist.by/api/v4/contest/"
CLIST_PAGE_SIZE = 100


def fetch_json(url: str, params: dict[str, object] | None = None) -> object:
    if params:
        encoded_params = urllib.parse.urlencode(params, doseq=True)
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        url = f"{url}{separator}{encoded_params}"

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"CLIST contests API request failed with HTTP {exc.code}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"CLIST contests API request failed: {exc.reason}") from exc


def parse_contest_time(value: object) -> datetime:
    return datetime.fromisoformat(str(value)).astimezone(TARGET_TIMEZONE)


def start_time(contest: dict[str, object]) -> datetime:
    return parse_contest_time(contest["start"])


def latest_start_time(contest: dict[str, object]) -> datetime:
    return start_time(contest)


def due_time(contest: dict[str, object], start_time: datetime) -> datetime:
    return latest_start_time(contest)


def build_issue_title(contest: dict[str, object], start_time: datetime) -> str:
    return f"DMOJ {contest['event']} - {format_time(start_time)}"


def build_issue_description(contest: dict[str, object], start_time: datetime) -> str:
    end_time = parse_contest_time(contest["end"])
    duration = end_time - start_time
    total_minutes = int(duration.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    duration_text = f"{hours}h" + (f" {minutes}m" if minutes else "")
    return "\n".join(
        [
            f"{contest['event']} starts on {format_date(start_time)} at {format_time(start_time)}.",
            f"Duration: {duration_text}",
            "Latest start: contest start time",
            f"URL: {contest.get('href') or 'Unavailable'}",
        ]
    )


def prefix(contest: dict[str, object], start_time: datetime) -> str:
    return f"{format_date(start_time)}: {contest['event']}"


def fetch_contests() -> list[dict[str, object]]:
    username = require_env("CLIST_USERNAME")
    api_key = require_env("CLIST_API_KEY")

    contests: list[dict[str, object]] = []
    offset = 0

    while True:
        payload = fetch_json(
            CLIST_CONTESTS_URL,
            params={
                "username": username,
                "api_key": api_key,
                "format": "json",
                "host": "dmoj.ca",
                "order_by": "start",
                "upcoming": "true",
                "limit": CLIST_PAGE_SIZE,
                "offset": offset,
            },
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected CLIST response")

        objects = payload.get("objects")
        if not isinstance(objects, list):
            raise RuntimeError("CLIST returned no contests")

        contests.extend(contest for contest in objects if isinstance(contest, dict))

        if len(objects) < CLIST_PAGE_SIZE:
            break
        offset += CLIST_PAGE_SIZE

    now = datetime.now(TARGET_TIMEZONE)
    upcoming = [
        contest
        for contest in contests
        if contest.get("start") and latest_start_time(contest) > now
    ]
    upcoming.sort(key=latest_start_time)
    return upcoming


SOURCE = Source(
    name="clist_dmoj",
    empty_message="No startable DMOJ contests found in CLIST.",
    fetch_items=fetch_contests,
    start_time=start_time,
    title=build_issue_title,
    description=build_issue_description,
    prefix=prefix,
    due_time=due_time,
    fetch_error_prefix="Unable to fetch DMOJ contests from CLIST",
)


def main() -> None:
    from main import run

    raise SystemExit(run(["clist_dmoj"]))


if __name__ == "__main__":
    main()
