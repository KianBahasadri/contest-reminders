import json
import re
import urllib.request
from datetime import datetime
from html import unescape

from linear_sync import (
    Source,
    TARGET_TIMEZONE,
    USER_AGENT,
    format_date,
    format_time,
)


CTFTIME_EVENTS_URL = "https://ctftime.org/api/v1/events/"


def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clean_text(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


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


def start_time(event: dict[str, object]) -> datetime:
    return datetime.fromisoformat(str(event["start"])).astimezone(TARGET_TIMEZONE)


def build_issue_description(event: dict[str, object], start_time: datetime) -> str:
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


def prefix(event: dict[str, object], start_time: datetime) -> str:
    return f"{format_date(start_time)}: {build_issue_title(event, start_time)}"


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


SOURCE = Source(
    name="ctf",
    empty_message=None,
    fetch_items=fetch_events,
    start_time=start_time,
    title=build_issue_title,
    description=build_issue_description,
    prefix=prefix,
    fetch_error_prefix="Unable to fetch CTFtime events",
)


def main() -> None:
    from main import run

    raise SystemExit(run(["ctf"]))


if __name__ == "__main__":
    main()
