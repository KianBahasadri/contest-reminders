import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


CLIST_CONTESTS_URL = "https://clist.by/api/v4/contest/"
LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
TARGET_TIMEZONE = ZoneInfo("America/New_York")
USER_AGENT = "Mozilla/5.0"
DEFAULT_PROJECT_NAME = "Competitions"
DEFAULT_BACKLOG_STATE_NAME = "Backlog"
DEFAULT_IN_PROGRESS_STATE_NAME = "In Progress"
LINEAR_URGENT_PRIORITY = 1
CLIST_PAGE_SIZE = 100


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


def parse_contest_time(value: object) -> datetime:
    return datetime.fromisoformat(str(value)).astimezone(TARGET_TIMEZONE)


def latest_start_time(contest: dict[str, object]) -> datetime:
    return parse_contest_time(contest["start"])


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


def sync_contest(
    api_key: str,
    team_id: str,
    project_id: str,
    backlog_state_id: str | None,
    in_progress_state_id: str | None,
    contest: dict[str, object],
    dry_run: bool,
) -> str:
    start_time = parse_contest_time(contest["start"])
    title = build_issue_title(contest, start_time)
    due_date = format_due_date(latest_start_time(contest))
    prefix = f"{format_date(start_time)}: {contest['event']}"

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
                return f"{prefix} [dry run: would update {existing_issue['identifier']} {' and '.join(updates)}]"

            update_issue_fields(
                api_key,
                str(existing_issue["id"]),
                title=title if existing_issue.get("title") != title else None,
                due_date=due_date
                if existing_issue.get("dueDate") != due_date
                else None,
                state_id=state_id,
            )
            return f"{prefix} [updated {existing_issue['identifier']} {' and '.join(updates)}]"

        return f"{prefix} [already existed as {existing_issue['identifier']}]"

    if dry_run:
        return f"{prefix} [dry run: would create {title!r}]"

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
    return f"{prefix} [created {issue_identifier}]"


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
    dry_run = env_truthy("CONTEST_REMINDER_DRY_RUN")

    try:
        contests = fetch_contests()
    except RuntimeError as exc:
        print(f"Unable to fetch DMOJ contests from CLIST: {exc}")
        raise SystemExit(1) from None

    if not contests:
        print("No startable DMOJ contests found in CLIST.")
        return

    for contest in contests:
        print(
            sync_contest(
                api_key,
                team_id,
                project_id,
                backlog_state_id,
                in_progress_state_id,
                contest,
                dry_run,
            )
        )


if __name__ == "__main__":
    main()
