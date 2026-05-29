import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

from linear_sync import (
    Source,
    TARGET_TIMEZONE,
    USER_AGENT,
    format_date,
    format_time,
)


ATCODER_CONTESTS_URL = "https://atcoder.jp/contests"
REQUEST_TIMEOUT_SECONDS = 20
FETCH_ATTEMPTS = 3
FETCH_RETRY_DELAY_SECONDS = 2


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


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_atcoder_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S%z").astimezone(TARGET_TIMEZONE)


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


def start_time(contest: dict[str, object]) -> datetime:
    return parse_atcoder_time(str(contest["start"]))


def build_issue_title(contest: dict[str, object], start_time: datetime) -> str:
    atcoder_class = contest_class(contest) or "Contest"
    return f"AtCoder {atcoder_class} {contest_number(contest)} - {format_time(start_time)}"


def build_issue_description(contest: dict[str, object], start_time: datetime) -> str:
    return "\n".join(
        [
            f"{contest['name']} starts on {format_date(start_time)} at {format_time(start_time)}.",
            f"Duration: {contest['duration']}",
            f"Rated range: {contest['rated_range']}",
            f"URL: https://atcoder.jp{contest['href']}",
        ]
    )


def prefix(contest: dict[str, object], start_time: datetime) -> str:
    return f"{format_date(start_time)}: {contest['name']}"


def fetch_contests() -> list[dict[str, object]]:
    html = fetch_text(ATCODER_CONTESTS_URL, headers={"User-Agent": USER_AGENT})
    parser = UpcomingContestParser()
    parser.feed(extract_upcoming_section(html))

    now = datetime.now(timezone.utc)
    upcoming: list[dict[str, object]] = [
        contest
        for contest in parser.contests
        if contest_class(contest) in {"ABC", "ARC"}
        and is_rated(contest)
        and parse_atcoder_time(contest["start"]).astimezone(timezone.utc) > now
    ]
    upcoming.sort(key=lambda contest: parse_atcoder_time(str(contest["start"])))
    return upcoming


SOURCE = Source(
    name="atcoder",
    empty_message="No rated AtCoder ABC/ARC contests found.",
    fetch_items=fetch_contests,
    start_time=start_time,
    title=build_issue_title,
    description=build_issue_description,
    prefix=prefix,
    fetch_error_prefix="Unable to fetch AtCoder contests",
)


def main() -> None:
    from main import run

    raise SystemExit(run(["atcoder"]))


if __name__ == "__main__":
    main()
