import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo


ATCODER_CONTESTS_URL = "https://atcoder.jp/contests"
LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
TARGET_TIMEZONE = ZoneInfo("America/New_York")
USER_AGENT = "Mozilla/5.0"
DEFAULT_PROJECT_NAME = "Competitions"
DEFAULT_BACKLOG_STATE_NAME = "Backlog"
DEFAULT_IN_PROGRESS_STATE_NAME = "In Progress"
LINEAR_URGENT_PRIORITY = 1
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


def fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
    last_error: Exception | None = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            last_error = exc
            message = f"AtCoder contests page request failed with HTTP {exc.code}"
            if exc.code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(message) from exc
        except urllib.error.URLError as exc:
            last_error = exc
            message = f"AtCoder contests page request failed: {exc.reason}"
        except TimeoutError as exc:
            last_error = exc
            message = "AtCoder contests page request timed out"

        if attempt < FETCH_ATTEMPTS:
            time.sleep(FETCH_RETRY_DELAY_SECONDS)
            continue

        raise RuntimeError(message) from last_error

    raise RuntimeError("AtCoder contests page request failed")


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


def format_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").replace("AM", "am").replace("PM", "pm")


def format_date(dt: datetime) -> str:
    return dt.strftime("%b %-d, %Y")


def format_due_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def parse_atcoder_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S%z").astimezone(TARGET_TIMEZONE)


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


class UpcomingContestParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contests: list[dict[str, str]] = []
        self.in_row = False
        self.in_cell = False
        self.current_cells: list[str] = []
        self.current_text: list[str] = []
        self.current_href = ""
        self.current_link_text: list[str] = []
        self.in_contest_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "tr":
            self.in_row = True
            self.current_cells = []
            return

        if self.in_row and tag == "td":
            self.in_cell = True
            self.current_text = []
            self.current_href = ""
            self.current_link_text = []
            self.in_contest_link = False
            return

        if self.in_cell and tag == "a":
            href = attrs_dict.get("href") or ""
            if href.startswith("/contests/"):
                self.current_href = href
                self.in_contest_link = True

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_text.append(data)
            if self.in_contest_link:
                self.current_link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.in_contest_link:
            self.in_contest_link = False
            return

        if tag == "td" and self.in_cell:
            if self.current_href:
                self.current_cells.append(
                    normalize_whitespace("".join(self.current_link_text))
                )
                self.current_cells.append(self.current_href)
            else:
                self.current_cells.append(normalize_whitespace("".join(self.current_text)))
            self.in_cell = False
            return

        if tag == "tr" and self.in_row:
            self.in_row = False
            if len(self.current_cells) >= 5:
                self.contests.append(
                    {
                        "start": self.current_cells[0],
                        "name": self.current_cells[1],
                        "href": self.current_cells[2],
                        "duration": self.current_cells[3],
                        "rated_range": self.current_cells[4],
                    }
                )


def extract_upcoming_section(html: str) -> str:
    marker = '<div id="contest-table-upcoming">'
    start = html.find(marker)
    if start == -1:
        raise RuntimeError("Could not find AtCoder upcoming contests table")

    tbody_start = html.find("<tbody>", start)
    tbody_end = html.find("</tbody>", tbody_start)
    if tbody_start == -1 or tbody_end == -1:
        raise RuntimeError("Could not find AtCoder upcoming contests rows")

    return html[tbody_start : tbody_end + len("</tbody>")]


def contest_class(contest: dict[str, str]) -> str | None:
    href = contest["href"]
    name = contest["name"]
    if re.fullmatch(r"/contests/abc\d+", href) or "AtCoder Beginner Contest" in name:
        return "ABC"
    if re.fullmatch(r"/contests/arc\d+", href) or "AtCoder Regular Contest" in name:
        return "ARC"
    return None


def is_rated(contest: dict[str, str]) -> bool:
    rated_range = contest["rated_range"].strip()
    return bool(rated_range and rated_range != "-" and rated_range.lower() != "all")


def contest_number(contest: dict[str, str]) -> str:
    match = re.search(r"/contests/(?:abc|arc)(\d+)", contest["href"])
    if match:
        return match.group(1)
    return contest["name"]


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


def build_issue_title(contest: dict[str, str], start_time: datetime) -> str:
    atcoder_class = contest_class(contest) or "Contest"
    return f"AtCoder {atcoder_class} {contest_number(contest)} - {format_time(start_time)}"


def build_issue_description(contest: dict[str, str], start_time: datetime) -> str:
    return "\n".join(
        [
            f"{contest['name']} starts on {format_date(start_time)} at {format_time(start_time)}.",
            f"Duration: {contest['duration']}",
            f"Rated range: {contest['rated_range']}",
            f"URL: https://atcoder.jp{contest['href']}",
        ]
    )


def fetch_contests() -> list[dict[str, str]]:
    html = fetch_text(ATCODER_CONTESTS_URL, headers={"User-Agent": USER_AGENT})
    parser = UpcomingContestParser()
    parser.feed(extract_upcoming_section(html))

    now = datetime.now(timezone.utc)
    upcoming = [
        contest
        for contest in parser.contests
        if contest_class(contest) in {"ABC", "ARC"}
        and is_rated(contest)
        and parse_atcoder_time(contest["start"]).astimezone(timezone.utc) > now
    ]
    upcoming.sort(key=lambda contest: parse_atcoder_time(contest["start"]))
    return upcoming


def sync_contest(
    api_key: str,
    team_id: str,
    project_id: str,
    backlog_state_id: str | None,
    in_progress_state_id: str | None,
    contest: dict[str, str],
    dry_run: bool,
) -> str:
    start_time = parse_atcoder_time(contest["start"])
    title = build_issue_title(contest, start_time)
    due_date = format_due_date(start_time)
    prefix = f"{format_date(start_time)}: {contest['name']}"

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
        print(f"Unable to fetch AtCoder contests: {exc}")
        raise SystemExit(1) from None

    if not contests:
        print("No rated AtCoder ABC/ARC contests found.")
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
