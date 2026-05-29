import json
import os
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
TARGET_TIMEZONE = ZoneInfo("America/New_York")
USER_AGENT = "Mozilla/5.0"
DEFAULT_PROJECT_NAME = "Competitions"
DEFAULT_BACKLOG_STATE_NAME = "Backlog"
DEFAULT_IN_PROGRESS_STATE_NAME = "In Progress"
LINEAR_URGENT_PRIORITY = 1


@dataclass(frozen=True)
class Source:
    name: str
    empty_message: str | None
    fetch_items: Callable[[], list[dict[str, object]]]
    start_time: Callable[[dict[str, object]], datetime]
    title: Callable[[dict[str, object], datetime], str]
    description: Callable[[dict[str, object], datetime], str]
    prefix: Callable[[dict[str, object], datetime], str]
    due_time: Callable[[dict[str, object], datetime], datetime] | None = None
    eligibility: Callable[[dict[str, object]], tuple[bool, str | None]] | None = None
    fetch_error_prefix: str | None = None


@dataclass(frozen=True)
class LinearConfig:
    api_key: str
    team_id: str
    project_id: str
    backlog_state_id: str | None
    in_progress_state_id: str | None
    dry_run: bool


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


def format_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").replace("AM", "am").replace("PM", "pm")


def format_date(dt: datetime) -> str:
    return dt.strftime("%b %-d, %Y")


def format_due_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


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


def resolve_team_states(api_key: str, team_id: str) -> list[dict[str, object]]:
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
    return list(data["team"]["states"]["nodes"])


def find_state_id(
    states: Iterable[dict[str, object]], state_name: str, state_type: str
) -> str | None:
    states = list(states)
    for state in states:
        if str(state["name"]).lower() == state_name.lower():
            return str(state["id"])

    for state in states:
        if state["type"] == state_type:
            return str(state["id"])

    return None


def load_linear_config() -> LinearConfig:
    api_key = require_env("LINEAR_API_KEY")
    team_id = require_env("LINEAR_TEAM_ID")
    project_name = os.environ.get("LINEAR_PROJECT_NAME", DEFAULT_PROJECT_NAME)
    project_id = os.environ.get("LINEAR_PROJECT_ID") or resolve_project_id(
        api_key, project_name
    )
    backlog_state_id = os.environ.get("LINEAR_BACKLOG_STATE_ID")
    in_progress_state_id = os.environ.get("LINEAR_IN_PROGRESS_STATE_ID")

    if not backlog_state_id or not in_progress_state_id:
        states = resolve_team_states(api_key, team_id)
        if not backlog_state_id:
            backlog_state_id = find_state_id(
                states,
                os.environ.get("LINEAR_BACKLOG_STATE_NAME", DEFAULT_BACKLOG_STATE_NAME),
                "backlog",
            )
        if not in_progress_state_id:
            in_progress_state_id = find_state_id(
                states,
                os.environ.get(
                    "LINEAR_IN_PROGRESS_STATE_NAME", DEFAULT_IN_PROGRESS_STATE_NAME
                ),
                "started",
            )
    return LinearConfig(
        api_key=api_key,
        team_id=team_id,
        project_id=project_id,
        backlog_state_id=backlog_state_id,
        in_progress_state_id=in_progress_state_id,
        dry_run=env_truthy("CONTEST_REMINDER_DRY_RUN"),
    )


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


def find_issue(
    api_key: str, title: str, project_id: str
) -> dict[str, object] | None:
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
    state_id: str | None,
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
    if state_id:
        issue_input["stateId"] = state_id

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


def sync_item(config: LinearConfig, source: Source, item: dict[str, object]) -> str:
    start_time = source.start_time(item)
    due_time = source.due_time(item, start_time) if source.due_time else start_time
    due_date = format_due_date(due_time)
    prefix = source.prefix(item, start_time)

    reason = None
    if source.eligibility:
        eligible, reason = source.eligibility(item)
        if not eligible:
            return f"{prefix} [{reason}]"

    title = source.title(item, start_time)
    existing_issue = find_issue(config.api_key, title, config.project_id)
    reason_suffix = f": {reason}" if reason else ""

    if existing_issue:
        updates: list[str] = []
        if existing_issue.get("title") != title:
            updates.append("title")
        if existing_issue.get("dueDate") != due_date:
            updates.append("due date")
        state_id = None
        if (
            config.in_progress_state_id
            and is_today_or_tomorrow(start_time)
            and should_start_issue(existing_issue)
        ):
            updates.append("state")
            state_id = config.in_progress_state_id

        if updates:
            if config.dry_run:
                return f"{prefix} [dry run: would update {existing_issue['identifier']} {' and '.join(updates)}{reason_suffix}]"

            update_issue_fields(
                config.api_key,
                str(existing_issue["id"]),
                title=title if existing_issue.get("title") != title else None,
                due_date=due_date
                if existing_issue.get("dueDate") != due_date
                else None,
                state_id=state_id,
            )
            return f"{prefix} [updated {existing_issue['identifier']} {' and '.join(updates)}{reason_suffix}]"

        return f"{prefix} [already existed as {existing_issue['identifier']}{reason_suffix}]"

    if config.dry_run:
        return f"{prefix} [dry run: would create {title!r}{reason_suffix}]"

    issue_state_id = (
        config.in_progress_state_id
        if is_today_or_tomorrow(start_time)
        else config.backlog_state_id
    )
    issue_identifier = create_issue(
        config.api_key,
        config.team_id,
        config.project_id,
        issue_state_id,
        title,
        source.description(item, start_time),
        due_date,
    )
    return f"{prefix} [created {issue_identifier}{reason_suffix}]"


def sync_source(config: LinearConfig, source: Source) -> list[str]:
    items = source.fetch_items()
    if not items and source.empty_message:
        return [source.empty_message]

    return [sync_item(config, source, item) for item in items]
