#!/usr/bin/env python3
"""Prepare a keyless, 2025+ eight-journal metadata review pull request.

The collectors check public journal issue pages, PubMed and Crossref. This
script writes bibliographic metadata, never source abstract text. GitHub Pages
changes only after a person reviews and merges the resulting pull request.
"""

from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from scrape_domestic_issues import gather as collect_domestic_issues
from scrape_kci_public import collect as collect_kci_issues
from scrape_international import collect as collect_international


ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / "content" / "auto-trends.json"
COVERAGE = ROOT / "content" / "trends-coverage.json"
START = dt.date(2025, 1, 1)
JOURNALS = {
    "보건경제와 정책연구", "보건사회연구",
    "Journal of Health Technology Assessment", "HIRA Research",
    "PharmacoEconomics", "Value in Health", "Health Economics",
    "Social Science & Medicine",
}


def publication_date(value: str) -> dt.date:
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        raise ValueError(f"Invalid publication date: {value}")
    return dt.date.fromisoformat((value + "-01-01")[:10])


def clean_record(record: dict, as_of: dt.date) -> dict:
    result = {key: value for key, value in record.items() if not key.startswith("_")}
    if result.pop("abstract_available_at_source", False):
        result["abstract_available"] = True
    if result.get("metadata_source") in {"KIHASA publisher issue HTML", "KCI public issue and article HTML"}:
        result["abstract_available"] = True
    if result["journal"] not in JOURNALS:
        raise ValueError(f"Unknown journal: {result['journal']}")
    if not START <= publication_date(result["published"]) <= as_of:
        raise ValueError(f"Out-of-range publication date: {result['id']}")
    if not all(isinstance(result.get(key), str) and result[key].strip()
               for key in ("id", "title", "authors", "published", "source_url")):
        raise ValueError(f"Incomplete bibliographic record: {result.get('id')}")
    if urlsplit(result["source_url"]).scheme != "https":
        raise ValueError(f"Non-HTTPS source URL: {result['id']}")
    doi = result.get("doi", "").lower().strip()
    if doi and (not re.fullmatch(r"10\.\d{4,9}/[^\s<>]+", doi, re.I) or result["id"] != "doi:" + doi):
        raise ValueError(f"DOI/identity mismatch: {result['id']}")
    result["doi"] = doi
    # Collectors may use a human-facing pending message; it is not a summary.
    # Keep only explicitly reviewed prose from the existing published dataset.
    result["abstract_summary"] = ""
    return result


def preserve_reviewed_summaries(entries: list[dict]) -> None:
    if not ARTICLES.exists():
        return
    old = json.loads(ARTICLES.read_text(encoding="utf-8"))
    if not isinstance(old, list):
        raise ValueError("Existing auto-trends.json is not an array")
    summaries = {item.get("id"): item.get("abstract_summary") for item in old
                 if item.get("editorial_status") == "reviewed" and item.get("abstract_summary")}
    for entry in entries:
        if entry["id"] in summaries:
            entry["abstract_summary"] = summaries[entry["id"]]
            entry["editorial_status"] = "reviewed"


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_bytes(path: Path, value: bytes) -> None:
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(value)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_pair(entries: list[dict], coverage: dict) -> bool:
    """Write two related files together, restoring the first if the second fails."""
    old_articles = ARTICLES.read_bytes() if ARTICLES.exists() else None
    old_coverage = COVERAGE.read_bytes() if COVERAGE.exists() else None
    if old_articles is not None and old_coverage is not None:
        previous_entries = json.loads(old_articles)
        previous_coverage = json.loads(old_coverage)
        comparable = lambda item: {key: value for key, value in item.items() if key != "as_of"}
        if entries == previous_entries and comparable(coverage) == comparable(previous_coverage):
            return False
    atomic_json(ARTICLES, entries)
    try:
        atomic_json(COVERAGE, coverage)
    except Exception:
        if old_articles is None:
            ARTICLES.unlink(missing_ok=True)
        else:
            atomic_bytes(ARTICLES, old_articles)
        raise
    return True


def collect(as_of: dt.date) -> tuple[list[dict], dict]:
    a, a_issues = collect_domestic_issues(as_of)
    b, b_issues = collect_kci_issues(as_of)
    c, international = collect_international(as_of)
    entries = [clean_record(record, as_of) for record in a + b + c]
    ids = [entry["id"] for entry in entries]
    if len(ids) != len(set(ids)):
        repeated = [ident for ident, count in Counter(ids).items() if count > 1]
        raise ValueError(f"Duplicate article identifiers: {repeated[:10]}")
    by_journal = Counter(entry["journal"] for entry in entries)
    if set(by_journal) != JOURNALS:
        raise ValueError(f"A journal returned no entries: {JOURNALS - set(by_journal)}")
    entries.sort(key=lambda entry: (entry["published"], entry["journal"], entry["id"]), reverse=True)
    coverage = {
        "as_of": as_of.isoformat(), "start": START.isoformat(), "total": len(entries),
        "by_journal": {journal: by_journal[journal] for journal in sorted(JOURNALS)},
        "domestic_issues": a_issues + b_issues,
        "international_sources": international,
        "scope": "Regular article candidates, including online-first items; meeting abstracts, editorials, letters, corrections and retraction notices excluded by source filters.",
        "summary_note": "Only separately reviewed short summaries are published; source abstracts stay at their original URLs.",
    }
    return entries, coverage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=dt.date.today().isoformat())
    args = parser.parse_args()
    try:
        as_of = dt.date.fromisoformat(args.as_of)
        if as_of < START:
            raise ValueError("The publication cutoff predates 2025")
        entries, coverage = collect(as_of)
        preserve_reviewed_summaries(entries)
        changed = publish_pair(entries, coverage)
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as exc:
        message = f"Research trends collection failed; existing files preserved: {exc}"
        print(message)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
                handle.write(message + "\n")
        return 1
    message = (f"{'Updated' if changed else 'No changes to'} {len(entries)} "
               f"2025+ regular candidates through {as_of}: {coverage['by_journal']}")
    print(message)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
