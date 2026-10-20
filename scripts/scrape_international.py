#!/usr/bin/env python3
"""Collect 2025+ metadata for four international G-HEP journal feeds.

Uses public PubMed E-utilities and Crossref REST without API keys. Returns a
metadata review queue, not publication-ready Korean abstract summaries.

Usage: python journal_metadata_no_key.py --as-of 2026-10-20 \
  --output international.json --coverage coverage.json
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

START = dt.date(2025, 1, 1)
JOURNALS = {
    'PharmacoEconomics': '1179-2027',
    'Value in Health': '1098-3015',
    'Health Economics': '1057-9230',
    'Social Science & Medicine': '0277-9536',
}
USER_AGENT = 'G-HEP journal metadata review (mailto:yifromseoul@gmail.com)'
EXCLUDED_TYPES = {'Editorial', 'Letter', 'Published Erratum', 'Retraction of Publication',
                  'Retracted Publication', 'Comment', 'News', 'Biography', 'Interview'}
EXCLUDED_TITLES = re.compile(
    r'^(?:correction(?: to)?|corrigendum|erratum|retraction(?: notice)?|'
    r'author reply|reply to|editorial board|table of contents|cover|issue information|'
    r'disclaimer|reviewer acknowledg(?:e)?ment|acknowledg(?:e)?ment to referees|'
    r'research presentations financial disclosure statements|withdrawn|obituary)\b', re.I)
DOI_PATTERN = re.compile(r'^10\.\d{4,9}/[^\s<>]+$', re.I)
CROSSREF_SELECT = ('DOI,title,author,ISSN,type,published-online,published-print,'
                   'published,issued,issue,page,volume,abstract,article-number')


def request_json_or_xml(url: str) -> bytes:
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={'User-Agent': USER_AGENT}), timeout=120) as response:
                if response.status != 200:
                    raise RuntimeError(f'HTTP {response.status}: {url.split("?")[0]}')
                return response.read(12_000_000)
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise
            delay = exc.headers.get('Retry-After', '')
            time.sleep(min(int(delay) if delay.isdigit() else 3 * (attempt + 1), 30))
        except (URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError('Network retry exhausted')


def clean(text: object) -> str:
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def node_text(node: ET.Element | None) -> str:
    return clean(''.join(node.itertext())) if node is not None else ''


def date_value(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat((value + '-01-01')[:10])
    except ValueError:
        return None


def xml_date(node: ET.Element | None) -> str:
    if node is None:
        return ''
    year = node_text(node.find('Year'))
    if not year:
        match = re.match(r'\d{4}', node_text(node.find('MedlineDate')))
        year = match.group() if match else ''
    if not year:
        return ''
    month = node_text(node.find('Month'))
    if month and not month.isdigit():
        try:
            month = str(dt.datetime.strptime(month[:3].title(), '%b').month)
        except ValueError:
            month = ''
    day = node_text(node.find('Day'))
    return year + ('-' + month.zfill(2) if month else '') + ('-' + day.zfill(2) if month and day else '')


def pubmed_article(node: ET.Element, journal: str) -> dict | None:
    article = node.find('./MedlineCitation/Article')
    if article is None:
        return None
    pmid = node_text(node.find('./MedlineCitation/PMID'))
    doi = next((node_text(x).lower() for x in node.findall('./PubmedData/ArticleIdList/ArticleId')
                if x.attrib.get('IdType') == 'doi'), '')
    authors = []
    for author in article.findall('./AuthorList/Author'):
        name = node_text(author.find('CollectiveName')) or clean(
            node_text(author.find('ForeName')) + ' ' + node_text(author.find('LastName')))
        if name:
            authors.append(name)
    abstract = ' '.join(node_text(x) for x in article.findall('./Abstract/AbstractText'))
    online_dates = [xml_date(x) for x in article.findall('./ArticleDate') if xml_date(x)]
    if not online_dates:
        online_dates = [xml_date(x) for x in node.findall('./PubmedData/History/PubMedPubDate')
                        if x.attrib.get('PubStatus') in {'epublish', 'aheadofprint'} and xml_date(x)]
    return {'pmid': pmid, 'doi': doi, 'title': node_text(article.find('ArticleTitle')),
            'authors': authors, 'abstract_available': bool(abstract),
            'types': {node_text(x) for x in article.findall('./PublicationTypeList/PublicationType')},
            'issue_date': xml_date(article.find('./Journal/JournalIssue/PubDate')),
            'online_date': min(online_dates) if online_dates else '',
            'page': node_text(article.find('./Pagination/MedlinePgn')),
            'issue': node_text(article.find('./Journal/JournalIssue/Issue')),
            'journal': journal}


def pubmed_search(journal: str, issn: str, as_of: dt.date) -> list[dict]:
    term = f'{issn}[issn] AND ("{START:%Y/%m/%d}"[dp]:"{as_of:%Y/%m/%d}"[dp])'
    url = ('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?' +
           urlencode({'db': 'pubmed', 'term': term, 'retmode': 'json', 'retmax': 0, 'usehistory': 'y'}))
    search = json.loads(request_json_or_xml(url))['esearchresult']
    count = int(search['count'])
    if count == 0:
        raise RuntimeError(f'PubMed returned zero records for {journal}; refusing partial update')
    result = []
    for pos in range(0, count, 200):
        params = {'db': 'pubmed', 'query_key': search['querykey'], 'WebEnv': search['webenv'],
                  'retstart': pos, 'retmax': 200, 'retmode': 'xml'}
        url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?' + urlencode(params)
        root = ET.fromstring(request_json_or_xml(url))
        batch = [pubmed_article(x, journal) for x in root.findall('PubmedArticle')]
        if len(batch) != min(200, count - pos) or any(x is None for x in batch):
            raise RuntimeError(f'PubMed EFetch truncated for {journal}: offset {pos}')
        result.extend(batch)
        time.sleep(0.36)  # NCBI's unauthenticated E-utilities limit is 3 requests/sec.
    return result


def crossref_search(journal: str, issn: str, as_of: dt.date) -> list[dict]:
    result = []
    cursor = '*'
    seen = set()
    while True:
        if cursor in seen:
            raise RuntimeError(f'Crossref repeated cursor for {journal}')
        seen.add(cursor)
        params = {'filter': f'from-pub-date:{START},until-pub-date:{as_of},type:journal-article',
                  'rows': 1000, 'cursor': cursor, 'select': CROSSREF_SELECT}
        url = f'https://api.crossref.org/journals/{issn}/works?' + urlencode(params)
        message = json.loads(request_json_or_xml(url))['message']
        items = message['items']
        result.extend(items)
        if len(result) >= message['total-results'] or len(items) < 1000:
            break
        cursor = message['next-cursor']
        time.sleep(0.5)
    if not result:
        raise RuntimeError(f'Crossref returned zero records for {journal}; refusing partial update')
    return result


def crossref_date(item: dict, field: str) -> str:
    values = (item.get(field) or {}).get('date-parts', [[]])[0]
    if not values:
        return ''
    try:
        formatted = f'{int(values[0]):04d}'
        if len(values) >= 2:
            formatted += f'-{int(values[1]):02d}'
        if len(values) >= 3:
            formatted += f'-{int(values[2]):02d}'
        return formatted if date_value(formatted) else ''
    except (ValueError, TypeError):
        return ''


def selected_date(online: str, issue: str, as_of: dt.date) -> str:
    # A 2025 issue article first posted in 2024 belongs in the 2025 issue.
    # For 2025+ online-first articles, use their first online date.
    online_date, issue_date = date_value(online), date_value(issue)
    if online_date and START <= online_date <= as_of:
        return online
    if issue_date and START <= issue_date <= as_of:
        return issue
    return ''


def accept_pubmed(item: dict, as_of: dt.date) -> tuple[bool, str]:
    if item['types'] & EXCLUDED_TYPES:
        return False, 'publication-type'
    if EXCLUDED_TITLES.match(item['title']):
        return False, 'non-paper-title'
    if item['page'].upper().startswith('S') or 'supplement' in item['issue'].lower():
        return False, 'supplement'
    if not item['pmid'] or not item['title'] or not item['authors']:
        return False, 'incomplete-core-fields'
    if not selected_date(item['online_date'], item['issue_date'], as_of):
        return False, 'date-outside-period'
    return True, ''


def accept_crossref(item: dict, journal: str, issn: str, as_of: dt.date) -> tuple[bool, str]:
    title = clean((item.get('title') or [''])[0])
    doi = clean(item.get('DOI')).lower()
    authors = item.get('author') or []
    page = clean(item.get('page'))
    issue = clean(item.get('issue'))
    if issn not in item.get('ISSN', []):
        return False, 'journal-mismatch'
    if EXCLUDED_TITLES.match(title) or re.match(r'^10\.1016/s\d{4}-\d{4}\(',doi):
        return False, 'non-paper-title-or-issue-front-matter'
    if page.upper().startswith('S') or 'supplement' in issue.lower():
        return False, 'supplement'
    if journal == 'Value in Health' and not doi.startswith('10.1016/j.jval.'):
        return False, 'non-research-doi-pattern'
    if not DOI_PATTERN.fullmatch(doi) or not title or not authors:
        return False, 'incomplete-core-fields'
    online = crossref_date(item, 'published-online')
    issue_date = crossref_date(item, 'published-print') or crossref_date(item, 'issued')
    if not selected_date(online, issue_date, as_of):
        return False, 'date-outside-period'
    return True, ''


def collect(as_of: dt.date) -> tuple[list[dict], dict]:
    if as_of < START:
        raise ValueError(f'as_of must be on or after {START}')
    all_records = {}
    coverage = {}
    for journal, issn in JOURNALS.items():
        pubmed = pubmed_search(journal, issn, as_of)
        crossref = crossref_search(journal, issn, as_of)
        journal_records = {}
        excluded = collections.Counter()
        crossref_added = 0
        # A Crossref registration must not reintroduce a PubMed-indexed
        # editorial, letter or correction rejected below.
        pubmed_dois = {item['doi'] for item in pubmed if DOI_PATTERN.fullmatch(item['doi'])}
        for item in pubmed:
            okay, reason = accept_pubmed(item, as_of)
            if not okay:
                excluded['PubMed:'+reason] += 1
                continue
            doi = item['doi'] if DOI_PATTERN.fullmatch(item['doi']) else ''
            identity = 'doi:'+doi if doi else 'pmid:'+item['pmid']
            date = selected_date(item['online_date'], item['issue_date'], as_of)
            journal_records[identity] = {
                'id':identity, 'title':item['title'], 'journal':journal, 'scope':'international',
                'published':date, 'issue_published':item['issue_date'],
                'authors':'; '.join(item['authors']), 'abstract_summary':'',
                'abstract_available':item['abstract_available'], 'doi':doi,
                'source_url':('https://doi.org/'+quote(doi,safe='/') if doi else
                              'https://pubmed.ncbi.nlm.nih.gov/'+item['pmid']+'/'),
                'abstract_url':'https://pubmed.ncbi.nlm.nih.gov/'+item['pmid']+'/',
                'metadata_source':'PubMed', 'editorial_status':'metadata_only'}
        for item in crossref:
            doi = clean(item.get('DOI')).lower()
            identity = 'doi:'+doi
            if doi in pubmed_dois or identity in journal_records:
                continue
            okay, reason = accept_crossref(item, journal, issn, as_of)
            if not okay:
                excluded['Crossref:'+reason] += 1
                continue
            online = crossref_date(item, 'published-online')
            issue_date = crossref_date(item, 'published-print') or crossref_date(item, 'issued')
            authors = [clean(clean(a.get('given'))+' '+clean(a.get('family'))) for a in item['author']]
            journal_records[identity] = {
                'id':identity, 'title':clean(item['title'][0]), 'journal':journal,
                'scope':'international', 'published':selected_date(online,issue_date,as_of),
                'issue_published':issue_date,'authors':'; '.join(filter(None,authors)),
                'abstract_summary':'', 'abstract_available':bool(item.get('abstract')),
                'doi':doi,'source_url':'https://doi.org/'+quote(doi,safe='/'),
                'abstract_url':'https://doi.org/'+quote(doi,safe='/'),
                'metadata_source':'Crossref (PubMed 미수록)','editorial_status':'metadata_only'}
            crossref_added += 1
        for identity, record in journal_records.items():
            if identity in all_records:
                raise RuntimeError(f'Duplicate stable identifier across journals: {identity}')
            all_records[identity] = record
        coverage[journal] = {'pubmed_raw':len(pubmed),'crossref_raw':len(crossref),
                             'records':len(journal_records), 'crossref_only_added':crossref_added,
                             'source_abstract_available':sum(bool(x['abstract_available']) for x in journal_records.values()),
                             'excluded':dict(excluded)}
    records = sorted(all_records.values(), key=lambda x:(x['published'],x['journal'],x['id']),reverse=True)
    return records, coverage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--as-of',default=dt.date.today().isoformat())
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--coverage',type=Path,required=True)
    args = parser.parse_args()
    as_of=dt.date.fromisoformat(args.as_of)
    records, coverage=collect(as_of)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.coverage.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    args.coverage.write_text(json.dumps(coverage,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(coverage,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
