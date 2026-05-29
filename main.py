from collections.abc import Sequence
from pathlib import Path
import os

import atcoder
import clist_dmoj
import codeforces
import ctf
import dmoj
from linear_sync import load_dotenv, load_linear_config, sync_source


SOURCES = {
    "ctf": ctf.SOURCE,
    "codeforces": codeforces.SOURCE,
    "atcoder": atcoder.SOURCE,
    "clist_dmoj": clist_dmoj.SOURCE,
    "dmoj": dmoj.SOURCE,
}
SOURCE_ORDER = ("ctf", "codeforces", "atcoder", "clist_dmoj", "dmoj")


def enabled_source_names() -> set[str]:
    enabled_scripts = os.environ.get("CONTEST_REMINDER_SCRIPTS", "")
    if not enabled_scripts:
        raise RuntimeError("CONTEST_REMINDER_SCRIPTS must be set in .env")

    normalized = {name for name in enabled_scripts.replace(",", " ").split() if name}
    if "all" in normalized:
        return set(SOURCE_ORDER)
    return normalized


def run(source_names: Sequence[str] | None = None) -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    enabled = set(source_names) if source_names is not None else enabled_source_names()
    unknown = sorted(enabled - set(SOURCES))
    if unknown:
        raise RuntimeError(f"Unknown contest reminder source(s): {', '.join(unknown)}")

    config = load_linear_config()
    failed_sources: list[str] = []
    source_order: Sequence[str] = source_names if source_names is not None else SOURCE_ORDER

    for source_name in source_order:
        if source_name not in enabled:
            print(f"[{source_name}] skipped")
            continue

        source = SOURCES[source_name]
        try:
            for line in sync_source(config, source):
                print(f"[{source_name}] {line}")
        except RuntimeError as exc:
            prefix = source.fetch_error_prefix or f"Unable to fetch {source_name}"
            print(f"[{source_name}] {prefix}: {exc}")
            failed_sources.append(source_name)

    return 1 if failed_sources else 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
