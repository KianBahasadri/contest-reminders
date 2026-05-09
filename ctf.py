import json
import os
import re
import urllib.request
from datetime import datetime
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo


CTFTIME_EVENTS_URL = "https://ctftime.org/api/v1/events/"
LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
TARGET_TIMEZONE = ZoneInfo("America/New_York")
USER_AGENT = "Mozilla/5.0"
DEFAULT_PROJECT_NAME = "Competitions"
DEFAULT_BACKLOG_STATE_NAME = "Backlog"


def load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value

    raise RuntimeError(f"Missing required environment variable: {name}")


def env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


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


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clean_text(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def format_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").replace("AM", "am").replace("PM", "pm")


def format_date(dt: datetime) -> str:
    return dt.strftime("%b %-d, %Y")


def format_due_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def is_eligible_event(event: dict[str, object]) -> bool:
    return (
        event.get("format") == "Jeopardy"
        and not event.get("onsite")
        and float(event.get("weight", 0) or 0) > 0
        and event.get("restrictions") == "Open"
    )


def summarize_event(event: dict[str, object]) -> str:
    cleaned_description = clean_text(str(event.get("description", "")))
    sentences = re.split(r"(?<=[.!?])\s+", normalize_whitespace(cleaned_description))
    for sentence in sentences:
        sentence = sentence.strip(" -")
        if sentence and not sentence.lower().startswith("http"):
            return sentence

    title = str(event["title"])
    return f"{title} is an open online jeopardy-style CTF listed on CTFtime."


def classify_link(url: str, fallback_index: int) -> str:
    lowered = url.lower()
    if "discord.gg" in lowered or "discord.com" in lowered:
        return "Discord"
    if "ctftime.org" in lowered:
        return "CTFtime"
    if fallback_index == 1:
        return "Website"
    return f"Link {fallback_index}"


def extract_links(event: dict[str, object]) -> list[tuple[str, str]]:
    ordered_urls: list[str] = []
    for field in ("url", "ctftime_url", "live_feed"):
        value = str(event.get(field, "")).strip()
        if value and value not in ordered_urls:
            ordered_urls.append(value)

    description = clean_text(str(event.get("description", "")))
    for match in re.findall(r"https?://[^\s)>'\"]+", description):
        url = match.rstrip(".,")
        if url not in ordered_urls:
            ordered_urls.append(url)

    return [
        (classify_link(url, index), url)
        for index, url in enumerate(ordered_urls, start=1)
    ]


def build_issue_description(event: dict[str, object]) -> str:
    lines = [summarize_event(event)]
    links = extract_links(event)
    if links:
        lines.append("")
        for label, url in links:
            lines.append(f"- {label}: {url}")
    return "\n".join(lines)


def build_issue_title(event: dict[str, object], start_time: datetime) -> str:
    weight = float(event.get("weight", 0) or 0)
    return f"CTF:{weight:.2f} {event['title']} - {format_time(start_time)}"


def fetch_events() -> list[dict[str, object]]:
    events = fetch_json(CTFTIME_EVENTS_URL, headers={"User-Agent": USER_AGENT})
    if not isinstance(events, list):
        raise RuntimeError("Unexpected CTFtime response")

    eligible_events: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, dict) or not is_eligible_event(event):
            continue

        eligible_events.append(event)

    eligible_events.sort(key=lambda event: str(event["start"]))
    return eligible_events


def resolve_project_id(api_key: str, project_name: str) -> str:
    data = linear_graphql(
        api_key,
        """
        query ProjectByName($projectName: String!) {
          projects(filter: { name: { eq: $projectName } }) {
            nodes {
              id
              name
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
    return projects[0]["id"]


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
        if state["name"].lower() == backlog_state_name.lower():
            return state["id"]

    for state in states:
        if state["type"] == "backlog":
            return state["id"]

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
) -> None:
    issue_input: dict[str, object] = {}
    if title is not None:
        issue_input["title"] = title
    if due_date is not None:
        issue_input["dueDate"] = due_date
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
              id
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

    return created["issue"]["identifier"]


def sync_event(
    api_key: str,
    team_id: str,
    project_id: str,
    backlog_state_id: str | None,
    event: dict[str, object],
    dry_run: bool,
) -> str:
    start_time = datetime.fromisoformat(str(event["start"])).astimezone(TARGET_TIMEZONE)
    title = build_issue_title(event, start_time)
    due_date = format_due_date(start_time)

    existing_issue = find_issue(api_key, title, project_id)

    if existing_issue:
        updates: list[str] = []
        if existing_issue.get("title") != title:
            updates.append("title")
        if existing_issue.get("dueDate") != due_date:
            updates.append("due date")

        if updates:
            if dry_run:
                return f"{format_date(start_time)}: {title} [dry run: would update {existing_issue['identifier']} {' and '.join(updates)}]"

            update_issue_fields(
                api_key,
                str(existing_issue["id"]),
                title=title if existing_issue.get("title") != title else None,
                due_date=due_date
                if existing_issue.get("dueDate") != due_date
                else None,
            )
            return f"{format_date(start_time)}: {title} [updated {existing_issue['identifier']} {' and '.join(updates)}]"
        return f"{format_date(start_time)}: {title} [already existed]"

    if dry_run:
        return f"{format_date(start_time)}: {title} [dry run: would create {title!r}]"

    issue_identifier = create_issue(
        api_key,
        team_id,
        project_id,
        backlog_state_id,
        title,
        build_issue_description(event),
        due_date,
    )
    return f"{format_date(start_time)}: {title} [created {issue_identifier}]"


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
    dry_run = env_truthy("CONTEST_REMINDER_DRY_RUN")

    for event in fetch_events():
        print(sync_event(api_key, team_id, project_id, backlog_state_id, event, dry_run))


if __name__ == "__main__":
    main()
