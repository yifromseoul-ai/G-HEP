#!/usr/bin/env python3
"""Harvest 2025+ issue tables from two public Korean journal websites.

No KCI API key is used. This script deliberately fails on missing issues,
partial article lists, or missing source metadata. It creates files for
editorial review rather than publishing copied abstracts to the site.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import time
import calendar
from urllib.parse import urljoin, urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from lxml import html

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "domestic_tmp"
KCI_BASE = "https://www.kci.go.kr"
KCI_HOME = KCI_BASE + "/kciportal/landing/journalHome.kci?sere_id=001714"
KIHASA_BASE = "https://www.kihasa.re.kr"
KIHASA_HOME = KIHASA_BASE + "/hswr/"
ABSTRACT_NOTE = "초록 요약은 검토 중입니다. 원문에서 초록을 확인해 주세요."
UA = "G-HEP bibliographic review (+mailto:yifromseoul@gmail.com)"


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def fetch(url: str, *, offline_file: str = "") -> bytes:
    if offline_file:
        return (CACHE / offline_file).read_bytes()
    last = None
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": UA})
            with urlopen(request, timeout=35) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                body = response.read(4_000_000)
                if len(body) < 500:
                    raise RuntimeError("unexpectedly short HTML")
                return body
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {exc.code}: {url}") from exc
            last = exc
            if attempt < 2:
                time.sleep(2 + 2 * attempt)
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(2 + 2 * attempt)
    raise RuntimeError(f"Cannot retrieve {url}: {last}")


def doc(data: bytes):
    tree = html.fromstring(data)
    if tree.xpath("//title") and any(
        p in clean(tree.xpath("//title")[0].text_content()).lower()
        for p in ("error", "페이지를 찾을 수", "접근 제한")
    ):
        raise RuntimeError("Website returned an error page")
    return tree


def expected_year_quarters(as_of: dt.date) -> set[tuple[int, int]]:
    if as_of < dt.date(2025, 3, 31):
        return set()
    return {
        (year, quarter)
        for year in range(2025, as_of.year + 1)
        for quarter in range(1, 5)
        if dt.date(year, quarter * 3, calendar.monthrange(year, quarter * 3)[1]) <= as_of
    }


def kci_issues(data: bytes, as_of: dt.date) -> list[tuple[int, int, str]]:
    tree = doc(data)
    result = []
    for a in tree.xpath('//a[contains(@href,"journalArticleList.kci")]'):
        vol_no = re.search(r"v\.(\d+)\s*no\.(\d+)", clean(a.text_content()), re.I)
        if not vol_no:
            continue
        vol, no = map(int, vol_no.groups())
        if (vol + 1994, no) not in expected_year_quarters(as_of):
            continue
        result.append((vol, no, urljoin(KCI_BASE, a.get("href"))))
    unique = {(vol, no): url for vol, no, url in result}
    expected = {(year - 1994, quarter) for year, quarter in expected_year_quarters(as_of)}
    if unique.keys() != expected:
        raise RuntimeError(f"KJHEP archive mismatch: expected {expected}, got {unique.keys()}")
    return sorted((vol, no, url) for (vol, no), url in unique.items())


def kci_issue(data: bytes, *, vol: int, no: int, url: str) -> tuple[list[dict], dict]:
    tree = doc(data)
    title = clean(" ".join(tree.xpath("//title/text()")))
    if f"Vol.{vol} No.{no}" not in title:
        raise RuntimeError(f"Wrong KCI issue returned for {vol}({no}): {title}")
    label = tree.xpath('//section[contains(@class,"journal-list-box")]//small')
    if len(label) != 1:
        raise RuntimeError(f"Missing KCI issue count: {url}")
    match = re.search(r"총 논문 수\s*:\s*(\d+)", clean(label[0].text_content()))
    if not match:
        raise RuntimeError(f"Unparseable KCI issue count: {url}")
    expected = int(match.group(1))
    cards = tree.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," jl-con ")]')
    if len(cards) != expected:
        raise RuntimeError(f"KCI partial issue {vol}({no}): {len(cards)} cards vs {expected} listed")
    result = []
    for card in cards:
        anchors = card.xpath('.//a[contains(concat(" ",normalize-space(@class)," ")," tit ")]')
        if len(anchors) != 1:
            raise RuntimeError(f"Missing unique article title in {url}")
        a = anchors[0]
        title = clean(a.text_content())
        href = urljoin(url, a.get("href"))
        if not re.search(r"arti_id=ART\d+", href):
            raise RuntimeError(f"Unexpected KCI article link {href}")
        info = clean(" ".join(card.xpath('.//p[contains(@class,"info")]/text()')))
        date_match = re.search(r"\b(20\d{2})\.(\d{2})\b", info)
        if not date_match:
            raise RuntimeError(f"Missing date for {title}")
        published = f"{date_match.group(1)}-{date_match.group(2)}"
        doi_links = card.xpath('.//p[contains(@class,"info")]//a[contains(@href,"doi.org/")]/@href')
        doi = doi_links[0].split("doi.org/", 1)[1].lower() if doi_links else ""
        abstract = clean(" ".join(card.xpath('.//div[contains(@class,"slide-con-box")]//text()')))
        result.append({
            "id": "doi:" + doi if doi else "kci:" + re.search(r"arti_id=(ART\d+)", href).group(1),
            "title": title,
            "journal": "보건경제와 정책연구",
            "scope": "domestic",
            "published": published,
            "authors": "",  # retrieve full list from citation_author in detail page
            "abstract_summary": ABSTRACT_NOTE,
            "doi": doi,
            "source_url": href,
            "metadata_source": "KCI public issue and article HTML",
            "editorial_status": "metadata_only",
            "_source_abstract": abstract,
            "_issue": f"{vol}({no})",
        })
    return result, {"journal": "보건경제와 정책연구", "issue": f"{vol}({no})", "source_url": url,
                    "listed": expected, "included": len(result), "excluded_editorial": 0}


def complete_kci_article(entry: dict, body: bytes) -> dict:
    tree = doc(body)
    title = clean(" ".join(tree.xpath('//meta[@name="citation_title"]/@content')))
    if title != entry["title"]:
        raise RuntimeError(f"KCI detail title mismatch: {entry['source_url']}")
    authors = [clean(x) for x in tree.xpath('//meta[@property="citation_author"]/@content') if clean(x)]
    if not authors:
        raise RuntimeError(f"KCI author list missing: {entry['source_url']}")
    detail_doi = clean(" ".join(tree.xpath('//meta[@name="citation_doi"]/@content'))).lower()
    if entry["doi"] and entry["doi"] != detail_doi:
        raise RuntimeError(f"KCI DOI mismatch: {entry['source_url']}")
    entry["doi"] = detail_doi or entry["doi"]
    entry["id"] = "doi:" + entry["doi"] if entry["doi"] else entry["id"]
    entry["authors"] = "; ".join(authors)
    abstract = clean(" ".join(tree.xpath('//meta[@name="citation_abstract"]/@content')))
    if abstract and entry["_source_abstract"] and abstract != entry["_source_abstract"]:
        # Some page templates replace line breaks; normalized text should match.
        raise RuntimeError(f"KCI abstract mismatch: {entry['source_url']}")
    entry["_source_abstract"] = abstract or entry["_source_abstract"]
    return entry


def kihasa_issues(data: bytes, as_of: dt.date) -> list[tuple[int, int, str]]:
    tree = doc(data)
    issues = {}
    for a in tree.xpath('//a[contains(@href,"/hswr/v.")]'):
        match = re.fullmatch(r"/hswr/v\.(\d+)/(\d)/(20\d{2})", a.get("href") or "")
        if not match:
            continue
        vol, no, year = map(int, match.groups())
        if vol + 1980 != year or (year, no) not in expected_year_quarters(as_of):
            continue
        issues[vol, no] = urljoin(KIHASA_BASE, a.get("href"))
    expected = {(year - 1980, quarter) for year, quarter in expected_year_quarters(as_of)}
    if issues.keys() != expected:
        raise RuntimeError(f"HSWR archive mismatch: expected {expected}, got {issues.keys()}")
    return sorted((vol, no, url) for (vol, no), url in issues.items())


def kihasa_issue(data: bytes, *, vol: int, no: int, url: str) -> tuple[list[dict], dict]:
    tree = doc(data)
    head = clean(" ".join(tree.xpath('//div[contains(@class,"issue_title")]//h4/text()')))
    if f"제{vol}권 제{no}호" not in head:
        raise RuntimeError(f"Wrong HSWR issue {vol}({no}): {head}")
    cards = tree.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," issue_article ")]')
    if not cards:
        raise RuntimeError(f"Empty HSWR issue {vol}({no})")
    result = []
    excluded = 0
    for card in cards:
        title_link = card.xpath('.//a[contains(concat(" ",normalize-space(@class)," ")," ia_title ")]')
        if len(title_link) != 1:
            raise RuntimeError(f"Missing HSWR title: {url}")
        if card.xpath('.//*[contains(@class,"editorial_state")]'):
            excluded += 1
            continue
        a = title_link[0]
        title = clean(" ".join(a.xpath('text()')))
        full = urljoin(KIHASA_BASE, a.get("href"))
        bottoms = card.xpath('.//div[contains(@class,"il_bottom")]/span')
        if len(bottoms) < 2:
            raise RuntimeError(f"HSWR authors missing: {full}")
        korean_names = clean(" ".join(bottoms[0].xpath('text()[following-sibling::br]')))
        if not korean_names:
            korean_names = clean(" ".join(bottoms[0].xpath('text()')[:1]))
        authors = clean(re.sub(r"\([^)]*\)", "", korean_names).replace(" ; ", "; "))
        doi_urls = card.xpath('.//a[contains(@class,"doi_link")]/@href')
        doi = doi_urls[0].split("doi.org/", 1)[1].lower() if doi_urls else ""
        summary = card.xpath('.//div[contains(@class,"list_abstract")]/p')
        # The bilingual abstract is on this same issue page, immediately below the article.
        abstracts = [clean(x.text_content()) for x in summary if clean(x.text_content())]
        korean_abstract = abstracts[-1] if abstracts else ""
        if not (title and authors and full and doi and korean_abstract):
            raise RuntimeError(f"HSWR metadata/abstract missing: {full}")
        date = f"{vol + 1980}-{no * 3:02d}"  # volume 45 = year 2025; month given by quarterly issue
        result.append({
            "id": "doi:" + doi,
            "title": title,
            "journal": "보건사회연구",
            "scope": "domestic",
            "published": date,
            "authors": authors,
            "abstract_summary": ABSTRACT_NOTE,
            "doi": doi,
            "source_url": full,
            "metadata_source": "KIHASA publisher issue HTML",
            "editorial_status": "metadata_only",
            "_source_abstract": korean_abstract,
            "_issue": f"{vol}({no})",
        })
    if len(result) + excluded != len(cards):
        raise RuntimeError(f"HSWR partial issue {vol}({no})")
    return result, {"journal": "보건사회연구", "issue": f"{vol}({no})", "source_url": url,
                    "listed": len(cards), "included": len(result), "excluded_editorial": excluded}


def gather(as_of: dt.date) -> tuple[list[dict], list[dict]]:
    # Fetch archive pages first and require each expected quarterly issue.
    kci_index, hswr_index = fetch(KCI_HOME), fetch(KIHASA_HOME)
    planned = []
    for vol, no, url in kci_issues(kci_index, as_of):
        planned.append(("kci", vol, no, url))
    for vol, no, url in kihasa_issues(hswr_index, as_of):
        planned.append(("kihasa", vol, no, url))
    issue_results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, url): (kind, vol, no, url) for kind, vol, no, url in planned}
        for future in as_completed(futures):
            kind, vol, no, url = futures[future]
            body = future.result()
            issue_results[kind, vol, no] = (
                kci_issue(body, vol=vol, no=no, url=url)
                if kind == "kci" else kihasa_issue(body, vol=vol, no=no, url=url)
            )
            print(kind, vol, no, issue_results[kind, vol, no][1], flush=True)
    records, coverage = [], []
    for key in sorted(issue_results):
        found, summary = issue_results[key]
        records.extend(found)
        coverage.append(summary)
    kci_records = [r for r in records if r["journal"] == "보건경제와 정책연구"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, r["source_url"]): r for r in kci_records}
        for future in as_completed(futures):
            complete_kci_article(futures[future], future.result())
    ids = [r["id"] for r in records]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Duplicate article identifiers")
    if any(not all(r.get(k) for k in ("title", "authors", "published", "source_url", "_source_abstract")) for r in records):
        raise RuntimeError("Missing required metadata/abstract")
    return sorted(records, key=lambda r: (r["published"], r["journal"], r["title"]), reverse=True), coverage


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=dt.date.today().isoformat(), help="YYYY-MM-DD, inclusive")
    parser.add_argument("--output-dir", type=Path, default=ROOT, help="Directory for the three JSON files")
    args = parser.parse_args()
    as_of = dt.date.fromisoformat(args.as_of)
    entries, coverage = gather(as_of)
    public = [{k: v for k, v in item.items() if not k.startswith("_")} for item in entries]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "research-domestic-trends.json").write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n")
    (args.output_dir / "research-domestic-abstracts.json").write_text(json.dumps([
        {"id": item["id"], "source_url": item["source_url"], "abstract": item["_source_abstract"]}
        for item in entries], ensure_ascii=False, indent=2) + "\n")
    (args.output_dir / "research-domestic-coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n")
    print("total", len(entries), "by journal", {j: sum(r["journal"] == j for r in entries) for j in {r["journal"] for r in entries}})
