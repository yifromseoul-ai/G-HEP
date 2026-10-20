"""Small checks for the eight-journal updater and the shipped snapshot."""

import datetime as dt
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main, mock

import scrape_international as international
import sync_trends as sync


class TrendUpdaterTests(TestCase):
    def test_snapshot_matches_coverage_and_eight_journals(self):
        articles = json.loads(sync.ARTICLES.read_text(encoding="utf-8"))
        coverage = json.loads(sync.COVERAGE.read_text(encoding="utf-8"))
        self.assertEqual(len(articles), coverage["total"])
        self.assertEqual(len({x["id"] for x in articles}), len(articles))
        self.assertEqual({x["journal"] for x in articles}, sync.JOURNALS)
        for item in articles:
            sync.clean_record(item, dt.date.fromisoformat(coverage["as_of"]))
            self.assertNotIn("_source_abstract", item)
        for journal in sync.JOURNALS:
            self.assertEqual(sum(x["journal"] == journal for x in articles), coverage["by_journal"][journal])

    def test_pubmed_rejected_letter_is_not_readded_from_crossref(self):
        item = {"pmid":"123", "doi":"10.1000/example", "title":"Reply to a study",
                "authors":["A Researcher"], "abstract_available":False,
                "types":{"Letter"},"issue_date":"2026-06", "online_date":"2026-05-01",
                "page":"100", "issue":"2"}
        crossref = {"type":"journal-article", "ISSN":["1179-2027"],
                    "DOI":"10.1000/example", "title":["Reply to a study"],
                    "author":[{"given":"A","family":"Researcher"}],
                    "published-online":{"date-parts":[[2026,5,1]]}}
        with mock.patch.object(international, "JOURNALS", {"PharmacoEconomics":"1179-2027"}), \
             mock.patch.object(international, "pubmed_search", return_value=[item]), \
             mock.patch.object(international, "crossref_search", return_value=[crossref]):
            records, info = international.collect(dt.date(2026, 10, 20))
        self.assertEqual(records, [])
        self.assertEqual(info["PharmacoEconomics"]["crossref_only_added"], 0)

    def test_missing_source_keeps_both_existing_files(self):
        with mock.patch.object(sync, "collect", side_effect=RuntimeError("source unavailable")), \
             mock.patch.object(sync, "atomic_json") as write, \
             mock.patch("sys.argv", ["sync_trends.py", "--as-of", "2026-10-20"]):
            self.assertEqual(sync.main(), 1)
            write.assert_not_called()

    def test_reviewed_summary_survives_next_run(self):
        records = [{"id":"doi:10.1000/example", "abstract_summary":"", "editorial_status":"metadata_only"}]
        old = [{"id":"doi:10.1000/example", "abstract_summary":"검토된 원문 기반 짧은 요약", "editorial_status":"reviewed"}]
        with mock.patch.object(Path, "exists", return_value=True), \
             mock.patch.object(Path, "read_text", return_value=json.dumps(old)):
            sync.preserve_reviewed_summaries(records)
        self.assertEqual(records[0]["editorial_status"], "reviewed")
        self.assertEqual(records[0]["abstract_summary"], old[0]["abstract_summary"])

    def test_second_file_failure_restores_first(self):
        with TemporaryDirectory() as directory:
            article_file = Path(directory) / "articles.json"
            coverage_file = Path(directory) / "coverage.json"
            article_file.write_text('[]\n', encoding="utf-8")
            coverage_file.write_text('{"as_of":"2026-10-19"}\n', encoding="utf-8")
            real_write = sync.atomic_json

            def fail_coverage(path, value):
                if path == coverage_file:
                    raise OSError("simulated second-file failure")
                return real_write(path, value)

            with mock.patch.object(sync, "ARTICLES", article_file), \
                 mock.patch.object(sync, "COVERAGE", coverage_file), \
                 mock.patch.object(sync, "atomic_json", side_effect=fail_coverage):
                with self.assertRaises(OSError):
                    sync.publish_pair([{"id":"new"}], {"as_of":"2026-10-20"})
            self.assertEqual(json.loads(article_file.read_text(encoding="utf-8")), [])
            self.assertEqual(json.loads(coverage_file.read_text(encoding="utf-8"))["as_of"], "2026-10-19")

    def test_date_only_change_does_not_write_files(self):
        with TemporaryDirectory() as directory:
            article_file = Path(directory) / "articles.json"
            coverage_file = Path(directory) / "coverage.json"
            entries = [{"id":"same"}]
            article_file.write_text(json.dumps(entries), encoding="utf-8")
            coverage_file.write_text(json.dumps({"as_of":"2026-10-19", "total":1}), encoding="utf-8")
            with mock.patch.object(sync, "ARTICLES", article_file), \
                 mock.patch.object(sync, "COVERAGE", coverage_file), \
                 mock.patch.object(sync, "atomic_json") as write:
                self.assertFalse(sync.publish_pair(entries, {"as_of":"2026-10-20", "total":1}))
                write.assert_not_called()


if __name__ == "__main__":
    main()
