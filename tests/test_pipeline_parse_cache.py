"""
Pipeline integration tests for the parse cache in streaming mode.

Uses __new__-constructed pipeline instances with fake downloader/parser/
analyzer so no network or real PDF is involved. Verifies that the second
run over the same announcement skips BOTH download and parse, and that
file_size_bytes is restored from the cache entry.
"""

from __future__ import annotations

import os

import pytest

from src.parse_cache import DiskParseCache, ParseCache
from src.pipeline import FinancialAnalysisPipeline


ANNOUNCEMENT = {
    "announcementTitle": "2020年年度报告",
    "adjunctUrl": "finalpage/2021-04-30/999.PDF",
    "announcementTime": "2021-04-30",
}
PDF_BYTES = b"%PDF-1.4 fake report bytes"


class FakeDownloader:
    def __init__(self, download_path):
        self.download_calls = 0
        self.download_path = str(download_path)

    def build_download_url(self, announcement):
        return "https://static.cninfo.com.cn/finalpage/999.PDF", "report.pdf"

    def download_one_sync(self, url, save_path):
        self.download_calls += 1
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "wb") as fh:
            fh.write(PDF_BYTES)
        return True


class FakeParser:
    def __init__(self):
        self.parse_calls = 0

    def parse_pdf(self, pdf_path, save_output=True):
        self.parse_calls += 1
        return {
            "pdf_path": pdf_path,
            "text": "本公司董事会治理稳健。" * 200,
            "mda_text": "一、经营情况讨论与分析。未来充满机遇。" * 100,
            "tables": [],
            "financial_statements": {},
            "text_path": "",
            "mda_path": "",
            "output_dir": "",
            "error": None,
        }


class FakeAnalyzer:
    def analyze_text(self, text, file_path=None):
        return {
            "tone_raw": 0.1,
            "fog_index": 2.0,
            "char_count": len(text),
            "file_size_bytes": 0,  # real analyzer stats the file; fake doesn't
            "success": True,
            "error": None,
        }


def _make_pipeline(tmp_path):
    p = FinancialAnalysisPipeline.__new__(FinancialAnalysisPipeline)
    p.downloader = FakeDownloader(tmp_path / "downloads")
    p.parser = FakeParser()
    p.analyzer = FakeAnalyzer()
    p.company_map = {}
    p.analysis_text_min_chars = 500
    p.analysis_text_min_ratio = 0.02
    p.parse_cache = ParseCache(
        disk=DiskParseCache(root=str(tmp_path / "parse_cache"), max_entries=10),
        remote=None,
    )
    return p


def _run(pipeline):
    return pipeline._process_single_report(
        stock_code="600000",
        year=2020,
        report_type="annual",
        announcement=ANNOUNCEMENT,
        delete_pdf=True,
        save_parsed_text=False,
    )


def test_second_run_skips_download_and_parse(tmp_path):
    p = _make_pipeline(tmp_path)

    first = _run(p)
    assert first is not None
    assert p.downloader.download_calls == 1
    assert p.parser.parse_calls == 1
    assert first["analysis_text_source"] in {"mda_text", "full_text"}
    assert first["file_path"] == ""  # delete_pdf=True

    second = _run(p)
    assert second is not None
    assert p.downloader.download_calls == 1, "cache hit must skip download"
    assert p.parser.parse_calls == 1, "cache hit must skip parse"
    # Same analytical output from the cached text.
    assert second["tone_raw"] == first["tone_raw"]
    assert second["char_count"] == first["char_count"]
    assert second["download_date"] == first["download_date"]
    assert second["announcement_title"] == first["announcement_title"]


def test_cache_hit_restores_file_size_bytes(tmp_path):
    p = _make_pipeline(tmp_path)
    _run(p)
    cached = _run(p)
    assert cached["file_size_bytes"] == len(PDF_BYTES)


def test_cache_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PARSE_CACHE", "off")
    p = _make_pipeline(tmp_path)
    p.parse_cache = ParseCache(disk=None, remote=None)

    first = _run(p)
    second = _run(p)
    assert p.downloader.download_calls == 2
    assert p.parser.parse_calls == 2
    assert first["tone_raw"] == second["tone_raw"]


def test_unusable_cache_entry_falls_back_to_download_and_parse(tmp_path):
    p = _make_pipeline(tmp_path)
    # Pre-populate the cache with an entry that yields no usable text.
    from src.parse_cache import cache_key_for_announcement
    p.parse_cache.put(
        cache_key_for_announcement(ANNOUNCEMENT),
        text="", mda_text="", file_size_bytes=0,
    )

    result = _run(p)
    assert result is not None, "must fall back to the normal path"
    assert p.downloader.download_calls == 1
    assert p.parser.parse_calls == 1


def test_new_pipeline_instance_shares_disk_cache(tmp_path):
    """Two jobs in the same Space uptime share the disk layer."""
    p1 = _make_pipeline(tmp_path)
    _run(p1)
    p2 = _make_pipeline(tmp_path)
    result = _run(p2)
    assert p2.downloader.download_calls == 0
    assert p2.parser.parse_calls == 0
    assert result["tone_raw"] == 0.1
