#!/usr/bin/env python3
"""Keyless public KCI issue-page collector for JoHTA and HIRA Research.

Run locally with `python scrape_kci_public.py`; it writes data.json and
coverage.json. Requires `lxml`, not an API key. The collector fails if an
expected issue or article page is empty so silent partial lists are avoided.
"""

import concurrent.futures as cf
import datetime as dt
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import html as html_entities

from lxml import html


ROOT = Path(__file__).resolve().parent
BASE = 'https://www.kci.go.kr/kciportal'
JOURNALS = {
    'Journal of Health Technology Assessment': ('SER000004879', {1: '06', 2: '12'}),
    'HIRA Research': ('SER000005933', {1: '05', 2: '11'}),
}
UA = 'G-HEP public scholarly metadata collector (mailto:yifromseoul@gmail.com)'


def fetch(url):
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={'User-Agent': UA}), timeout=35) as r:
                if r.status != 200:
                    raise RuntimeError(f'HTTP {r.status}: {url}')
                body = r.read(3_000_000)
                if len(body) < 5000:
                    raise RuntimeError(f'Short response: {url}')
                return html.fromstring(body)
        except Exception:
            if attempt == 3:
                raise
            time.sleep((attempt + 1) * 3)


def compact(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def issues(sere_id, as_of):
    url = BASE + '/po/search/poSereArtiList.kci?' + urlencode({'sereId': sere_id})
    page = fetch(url)
    found = {}
    for onclick in page.xpath('//a[contains(@onclick,"fncList(")]/@onclick'):
        m = re.search(r"fncList\('(20\d\d)','(VOL\d+)'\)", onclick)
        if m and 2025 <= int(m.group(1)) <= as_of.year:
            found[m.group(2)] = int(m.group(1))
    if not found:
        raise RuntimeError(f'No 2025+ issue detected for {sere_id}')
    return found


def article_ids(sere_id, issue_id):
    url = BASE + '/po/search/poSereArtiList.kci?' + urlencode({'sereId': sere_id, 'volIsseId': issue_id})
    page = fetch(url)
    if page.xpath('string(//input[@id="volIsseId"]/@value)') != issue_id:
        raise RuntimeError(f'Wrong issue body {issue_id}')
    links = page.xpath('//tbody/tr/td[contains(@class,"al_left")]/a[contains(@href,"artiId=ART")]/@href')
    ids = []
    for link in links:
        m = re.search(r'artiId=(ART\d+)', link)
        if m:
            ids.append(m.group(1))
    if not ids or len(ids) != len(set(ids)):
        raise RuntimeError(f'Empty/duplicate articles in {issue_id}: {ids}')
    return ids, url


def record(article_id, journal, sere_id, issue_id, year, month):
    url = BASE + '/ci/sereArticleSearch/ciSereArtiView.kci?' + urlencode({'sereArticleSearchBean.artiId': article_id})
    page = fetch(url)
    title = compact(page.xpath('string(//*[@id="artiTitle"])'))
    publisher = compact(page.xpath('string(//*[@id="hdnPublisher"]/@value)'))
    issue_link = page.xpath('//p[contains(@class,"vol")]/a/@href')
    listed_issue = re.search(r'volIsseId=(VOL\d+)', issue_link[0]).group(1) if issue_link and re.search(r'volIsseId=(VOL\d+)', issue_link[0]) else ''
    listed_year = re.search(r'^(20\d\d)', page.xpath('string(//*[@id="hdnVolInfo"]/@value)'))
    if not title or not publisher or listed_issue != issue_id or not listed_year or int(listed_year.group(1)) != year:
        raise RuntimeError(f'Mismatched detail for {article_id}: {journal}/{issue_id}')
    # KCI repeats the author list in a secondary popup block. Only the first
    # rendered author group is the article byline; ORCID links are separate.
    authors = page.xpath('(//div[@class="author"])[1]/a[contains(@href,"cretId=")]')
    author_names = []
    for author in authors:
        string = compact(' '.join(author.itertext())).split('/')[0].strip()
        if string:
            author_names.append(string)
    # Some KCI records contain English only, which is preserved verbatim.
    doi = compact(page.xpath('string(//*[@id="hdnDoi"]/@value)'))
    abstract = compact(page.xpath('string(//*[@id="korAbst"])') or page.xpath('string(//*[@id="engAbst"])'))
    if not author_names:
        raise RuntimeError(f'No author in {article_id}')
    return {
        'id': ('doi:' + doi.lower()) if doi else ('kci:' + article_id),
        'title': title,
        'journal': journal,
        'scope': 'domestic',
        'published': f'{year}-{month}',
        'authors': '; '.join(author_names),
        'abstract_summary': '초록 요약 검토 중 · 원문에서 확인',
        'doi': doi,
        'source_url': url,
        'metadata_source': 'KCI 공개 학술지 권호 및 논문 상세',
        'editorial_status': 'metadata_only',
        'abstract_available_at_source': bool(abstract),
        'publication_status': 'issue',
    }


def crossref_hira(as_of):
    """Include HIRA Online First, which KCI issue pages do not yet index."""
    endpoint = 'https://api.crossref.org/journals/2765-7353/works'
    cursor = '*'
    rows = 100
    found = []
    for _ in range(20):
        url = endpoint + '?' + urlencode({
            'filter': f'from-pub-date:2025-01-01,until-pub-date:{as_of.isoformat()},type:journal-article',
            'rows': rows, 'cursor': cursor,
            'select': 'DOI,title,author,ISSN,type,published-online,published-print,published,issued',
        })
        with urlopen(Request(url, headers={'User-Agent': UA}), timeout=40) as r:
            page = json.load(r)['message']
        items = page['items']
        found.extend(items)
        if len(found) >= page['total-results']:
            break
        if not items or not page.get('next-cursor'):
            raise RuntimeError('Crossref HIRA pagination truncated')
        cursor = page['next-cursor']
    else:
        raise RuntimeError('Crossref HIRA page limit exceeded')
    if not found:
        raise RuntimeError('Crossref HIRA collection returned zero articles')
    result = []
    for item in found:
        if item['type'] != 'journal-article' or '2765-7353' not in item.get('ISSN', []):
            continue
        doi = item['DOI'].lower()
        if not doi.startswith('10.52937/hira.'):
            continue
        dates = item.get('published-online', {}).get('date-parts') or item.get('published', {}).get('date-parts')
        if not dates or len(dates[0]) != 3:
            raise RuntimeError(f'Incomplete HIRA date: {doi}')
        published = dt.date(*dates[0]).isoformat()
        if published > as_of.isoformat():
            continue
        authors = '; '.join(compact(f"{a.get('given','')} {a.get('family','')}") for a in item.get('author', []))
        title = compact(html_entities.unescape(item.get('title', [''])[0]))
        if not authors or not title:
            raise RuntimeError(f'Missing Crossref HIRA title/author: {doi}')
        result.append({
            'id': 'doi:' + doi, 'title': title, 'journal': 'HIRA Research',
            'scope': 'domestic', 'published': published, 'authors': authors,
            'abstract_summary': '초록 요약 검토 중 · 원문에서 확인',
            'doi': doi, 'source_url': 'https://doi.org/' + doi,
            'metadata_source': 'Crossref, HIRA Research', 'editorial_status': 'metadata_only',
            'abstract_available_at_source': True,
            'publication_status': 'online-first',
        })
    if not result:
        raise RuntimeError('No matching HIRA DOI records')
    return result


def collect(as_of):
    """Return (all entries, issue coverage) through a specified publication date."""
    all_articles = []
    coverage = []
    for journal, (sere_id, month_for_issue) in JOURNALS.items():
        for issue_id, year in issues(sere_id, as_of).items():
            ids, url = article_ids(sere_id, issue_id)
            # Read issue number from the first paper and verify all details match.
            first_url = BASE + '/ci/sereArticleSearch/ciSereArtiView.kci?' + urlencode({'sereArticleSearchBean.artiId': ids[0]})
            first = fetch(first_url)
            vol = compact(first.xpath('string(//*[@id="hdnVolInfo"]/@value)'))
            issue_match = re.search(r'no\.(\d+)', vol)
            if not issue_match or int(issue_match.group(1)) not in month_for_issue:
                raise RuntimeError(f'Unknown issue month: {journal} {issue_id} / {vol}')
            month = month_for_issue[int(issue_match.group(1))]
            if f'{year}-{month}-01' > as_of.isoformat():
                continue
            with cf.ThreadPoolExecutor(max_workers=3) as pool:
                papers = list(pool.map(lambda aid: record(aid, journal, sere_id, issue_id, year, month), ids))
            all_articles += papers
            coverage.append({'journal': journal, 'year': year, 'issue_id': issue_id,
                             'issue_number': int(issue_match.group(1)), 'article_count': len(papers),
                             'source_url': url})
            print(journal, year, issue_id, len(papers), flush=True)
    dedup = {row['id']: row for row in all_articles}
    if len(dedup) != len(all_articles):
        raise RuntimeError('Duplicate article identifiers')
    by_doi = {row['doi'].lower(): row for row in all_articles if row['doi']}
    online_count = 0
    for row in crossref_hira(as_of):
        if row['doi'] in by_doi:
            # Online date is more precise than the issue month. Retain the
            # original-language title and full byline from the KCI page.
            by_doi[row['doi']]['published'] = row['published']
        else:
            all_articles.append(row)
            online_count += 1
    coverage.append({'journal': 'HIRA Research', 'status': 'online-first',
                     'article_count': online_count,
                     'source_url': 'https://www.hira-research.or.kr/journal/list.html?pn=ahead'})
    all_articles.sort(key=lambda x: (x['published'], x['journal'], x['id']), reverse=True)
    return all_articles, coverage


def main():
    all_articles, coverage = collect(dt.date.today())
    (ROOT / 'data.json').write_text(json.dumps(all_articles, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (ROOT / 'coverage.json').write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Total:', len(all_articles))


if __name__ == '__main__':
    main()
