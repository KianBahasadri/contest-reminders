import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


CODEFORCES_CONTESTS_URL = "https://codeforces.com/api/contest.list?gym=false"
LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
TARGET_TIMEZONE = ZoneInfo("America/New_York")
USER_AGENT = "Mozilla/5.0"
DEFAULT_PROJECT_NAME = "Competitions"
DEFAULT_BACKLOG_STATE_NAME = "Backlog"
DEFAULT_IN_PROGRESS_STATE_NAME = "In Progress"
LINEAR_URGENT_PRIORITY = 1
USER_RATING = 393
REQUEST_TIMEOUT_SECONDS = 20
FETCH_ATTEMPTS = 3
FETCH_RETRY_DELAY_SECONDS = 2


def load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")


def env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


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


def post_json(
    url: str, payload: dict[str, object], headers: dict[str, str]
) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def linear_graphql(
    api_key: str, query: str, variables: dict[str, object] | None = None
) -> dict[str, object]:
    response = post_json(
        LINEAR_GRAPHQL_URL,
        {"query": query, "variables": variables or {}},
        {
            "Authorization": api_key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    errors = response.get("errors")
    if errors:
        raise RuntimeError(f"Linear API error: {errors}")

    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Linear API returned no data")

    return data


def format_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").replace("AM", "am").replace("PM", "pm")


def format_date(dt: datetime) -> str:
    return dt.strftime("%b %-d, %Y")


def format_due_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def is_today_or_tomorrow(dt: datetime) -> bool:
    today = datetime.now(TARGET_TIMEZONE).date()
    return dt.date() in {today, today + timedelta(days=1)}


def should_start_issue(issue: dict[str, object]) -> bool:
    state = issue.get("state") or {}
    if not isinstance(state, dict):
        return False

    state_name = str(state.get("name", "")).strip().lower()
    state_type = str(state.get("type", "")).strip().lower()
    return state_type in {"backlog", "unstarted"} or state_name in {
        "backlog",
        "todo",
        "to do",
    }


def resolve_project_id(api_key: str, project_name: str) -> str:
    data = linear_graphql(
        api_key,
        """
        query ProjectByName($projectName: String!) {
          projects(filter: { name: { eq: $projectName } }) {
            nodes {
              id
            }
          }
        }
        """,
        {"projectName": project_name},
    )
    projects = data["projects"]["nodes"]
    if not projects:
        raise RuntimeError(f"Could not find Linear project named {project_name!r}")
    if len(projects) > 1:
        raise RuntimeError(
            f"Found multiple Linear projects named {project_name!r}; set LINEAR_PROJECT_ID in .env"
        )
    return str(projects[0]["id"])


def resolve_backlog_state_id(
    api_key: str, team_id: str, backlog_state_name: str
) -> str | None:
    data = linear_graphql(
        api_key,
        """
        query TeamStates($teamId: String!) {
          team(id: $teamId) {
            states {
              nodes {
                id
                name
                type
              }
            }
          }
        }
        """,
        {"teamId": team_id},
    )
    states = data["team"]["states"]["nodes"]

    for state in states:
        if str(state["name"]).lower() == backlog_state_name.lower():
            return str(state["id"])

    for state in states:
        if state["type"] == "backlog":
            return str(state["id"])

    return None


def resolve_in_progress_state_id(
    api_key: str, team_id: str, in_progress_state_name: str
) -> str | None:
    data = linear_graphql(
        api_key,
        """
        query TeamStates($teamId: String!) {
          team(id: $teamId) {
            states {
              nodes {
                id
                name
                type
              }
            }
          }
        }
        """,
        {"teamId": team_id},
    )
    states = data["team"]["states"]["nodes"]

    for state in states:
        if str(state["name"]).lower() == in_progress_state_name.lower():
            return str(state["id"])

    for state in states:
        if state["type"] == "started":
            return str(state["id"])

    return None


def find_issue(api_key: str, title: str, project_id: str) -> dict[str, object] | None:
    data = linear_graphql(
        api_key,
        """
        query IssuesByTitle($title: String!) {
          issues(filter: { title: { eq: $title } }) {
            nodes {
              id
              identifier
              title
              dueDate
              state {
                id
                name
                type
              }
              project {
                id
              }
            }
          }
        }
        """,
        {"title": title},
    )

    for issue in data["issues"]["nodes"]:
        project = issue.get("project") or {}
        if project.get("id") == project_id:
            return issue

    return None


def update_issue_fields(
    api_key: str,
    issue_id: str,
    *,
    title: str | None = None,
    due_date: str | None = None,
    state_id: str | None = None,
) -> None:
    issue_input: dict[str, object] = {}
    if title is not None:
        issue_input["title"] = title
    if due_date is not None:
        issue_input["dueDate"] = due_date
    if state_id is not None:
        issue_input["stateId"] = state_id
    if not issue_input:
        return

    data = linear_graphql(
        api_key,
        """
        mutation UpdateIssueFields($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
          }
        }
        """,
        {"id": issue_id, "input": issue_input},
    )
    if not data["issueUpdate"]["success"]:
        raise RuntimeError(f"Linear failed to update issue {issue_id!r}")


def create_issue(
    api_key: str,
    team_id: str,
    project_id: str,
    backlog_state_id: str | None,
    title: str,
    description: str,
    due_date: str,
) -> str:
    issue_input: dict[str, object] = {
        "teamId": team_id,
        "projectId": project_id,
        "title": title,
        "description": description,
        "dueDate": due_date,
        "priority": LINEAR_URGENT_PRIORITY,
    }
    if backlog_state_id:
        issue_input["stateId"] = backlog_state_id

    data = linear_graphql(
        api_key,
        """
        mutation CreateIssue($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue {
              identifier
            }
          }
        }
        """,
        {"input": issue_input},
    )
    created = data["issueCreate"]
    if not created["success"]:
        raise RuntimeError(f"Linear failed to create issue for {title!r}")
    return str(created["issue"]["identifier"])


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


def contest_reason(
    contest: dict[str, object], allowed_divisions: set[int]
) -> tuple[bool, str]:
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


def sync_contest(
    api_key: str,
    team_id: str,
    project_id: str,
    backlog_state_id: str | None,
    in_progress_state_id: str | None,
    contest: dict[str, object],
    allowed_divisions: set[int],
    dry_run: bool,
) -> str:
    start_time = datetime.fromtimestamp(
        int(contest["startTimeSeconds"]), tz=TARGET_TIMEZONE
    )
    due_date = format_due_date(start_time)
    eligible, reason = contest_reason(contest, allowed_divisions)
    prefix = f"{format_date(start_time)}: {contest['name']}"

    if not eligible:
        return f"{prefix} [{reason}]"

    title = build_issue_title(contest, start_time)
    existing_issue = find_issue(api_key, title, project_id)

    if existing_issue:
        updates: list[str] = []
        if existing_issue.get("title") != title:
            updates.append("title")
        if existing_issue.get("dueDate") != due_date:
            updates.append("due date")
        state_id = None
        if (
            in_progress_state_id
            and is_today_or_tomorrow(start_time)
            and should_start_issue(existing_issue)
        ):
            updates.append("state")
            state_id = in_progress_state_id

        if updates:
            if dry_run:
                return f"{prefix} [dry run: would update {existing_issue['identifier']} {' and '.join(updates)}: {reason}]"

            update_issue_fields(
                api_key,
                str(existing_issue["id"]),
                title=title if existing_issue.get("title") != title else None,
                due_date=due_date
                if existing_issue.get("dueDate") != due_date
                else None,
                state_id=state_id,
            )
            return f"{prefix} [updated {existing_issue['identifier']} {' and '.join(updates)}: {reason}]"

        return f"{prefix} [already existed as {existing_issue['identifier']}: {reason}]"

    if dry_run:
        return f"{prefix} [dry run: would create {title!r}: {reason}]"

    issue_state_id = (
        in_progress_state_id if is_today_or_tomorrow(start_time) else backlog_state_id
    )
    issue_identifier = create_issue(
        api_key,
        team_id,
        project_id,
        issue_state_id,
        title,
        build_issue_description(contest, start_time),
        due_date,
    )
    return f"{prefix} [created {issue_identifier}: {reason}]"


def main() -> None:
    load_dotenv(Path(__file__).with_name(".env"))

    api_key = require_env("LINEAR_API_KEY")
    team_id = require_env("LINEAR_TEAM_ID")
    project_name = os.environ.get("LINEAR_PROJECT_NAME", DEFAULT_PROJECT_NAME)
    project_id = os.environ.get("LINEAR_PROJECT_ID") or resolve_project_id(
        api_key, project_name
    )
    backlog_state_id = os.environ.get(
        "LINEAR_BACKLOG_STATE_ID"
    ) or resolve_backlog_state_id(
        api_key,
        team_id,
        os.environ.get("LINEAR_BACKLOG_STATE_NAME", DEFAULT_BACKLOG_STATE_NAME),
    )
    in_progress_state_id = os.environ.get(
        "LINEAR_IN_PROGRESS_STATE_ID"
    ) or resolve_in_progress_state_id(
        api_key,
        team_id,
        os.environ.get(
            "LINEAR_IN_PROGRESS_STATE_NAME", DEFAULT_IN_PROGRESS_STATE_NAME
        ),
    )
    allowed_divisions = allowed_divisions_for_rating(USER_RATING)
    dry_run = env_truthy("CONTEST_REMINDER_DRY_RUN")

    try:
        contests = fetch_contests()
    except RuntimeError as exc:
        print(f"Unable to fetch Codeforces contests: {exc}")
        raise SystemExit(1) from None

    for contest in contests:
        print(
            sync_contest(
                api_key,
                team_id,
                project_id,
                backlog_state_id,
                in_progress_state_id,
                contest,
                allowed_divisions,
                dry_run,
            )
        )


if __name__ == "__main__":
    main()
