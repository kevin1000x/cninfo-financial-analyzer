"""Directory tests use captured-shape CNINFO rows, never a live market dependency."""

import json

import pytest
import requests
from fastapi.testclient import TestClient

from api import main
from src import stock_catalog as catalog_module
from src.stock_catalog import CACHE_TTL_SECONDS, CatalogUnavailable, StockCatalog


ROWS = [
    {"code": "000001", "zwjc": "平安银行", "pinyin": "payh", "orgId": "gssz0000001"},
    {"code": "600519", "zwjc": "贵州茅台", "pinyin": "gzmt", "orgId": "gssh0600519"},
    {"code": "920001", "zwjc": "北交样例", "pinyin": "bjyl", "orgId": "test-only"},
]


class Response:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return {"stockList": self.rows}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_module.requests, "get", lambda *a, **kw: Response(ROWS))
    return StockCatalog(tmp_path / "stocks.json")


@pytest.mark.parametrize("query,code", [("茅台", "600519"), ("6005", "600519"), ("GZMT", "600519"),
                                        ("ｐａｙｈ", "000001"), ("平 安", "000001")])
def test_name_code_and_source_pinyin_lookup(catalog, query, code):
    result = catalog.search(query)
    assert [row["code"] for row in result["items"]] == [code]
    assert result["stale"] is False and result["source"] == "CNINFO"


def test_directory_does_not_claim_beijing_analysis_support(catalog):
    rows = {row["code"]: row for row in catalog.search("")["items"]}
    assert rows["920001"]["market"] == "BJ"
    assert rows["920001"]["supported"] is False
    assert rows["600519"]["supported"] is True
    assert "不代表" in catalog.search("")["notice"]


def test_keystrokes_use_one_cached_catalog(catalog, monkeypatch):
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return Response(ROWS)
    monkeypatch.setattr(catalog_module.requests, "get", fetch)
    for query in ["6", "60", "600", "茅台", "gzmt"]:
        catalog.search(query)
    assert calls == [catalog_module.STOCK_LIST_URL]


def test_invalid_refresh_keeps_previous_snapshot_and_reports_stale(catalog, monkeypatch):
    first = catalog.search("600519")
    monkeypatch.setattr(catalog_module.time, "time", lambda: catalog._updated + CACHE_TTL_SECONDS + 1)
    monkeypatch.setattr(catalog_module.requests, "get", lambda *a, **kw: Response([]))
    stale = catalog.search("600519")
    assert stale["items"] == first["items"]
    assert stale["updated_at"] == first["updated_at"] and stale["stale"] is True


def test_restart_serves_persisted_cache_during_outage(catalog, monkeypatch):
    expected = catalog.search("payh")

    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(catalog_module.requests, "get", offline)
    restored = StockCatalog(catalog.cache_path)
    assert restored.search("payh")["items"] == expected["items"]


def test_empty_cache_and_outage_are_not_fake_zero_results(tmp_path, monkeypatch):
    calls = []

    def offline(*args, **kwargs):
        calls.append(1)
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(catalog_module.requests, "get", offline)
    catalog = StockCatalog(tmp_path / "absent.json")
    for _ in range(2):
        with pytest.raises(CatalogUnavailable):
            catalog.search("茅台")
    assert len(calls) == 1  # Failed refreshes also have a backoff.


def test_bad_rows_are_excluded_and_codes_keep_leading_zero(catalog, monkeypatch):
    monkeypatch.setattr(catalog_module.requests, "get", lambda *a, **kw: Response(
        ROWS + [{"code": "123", "zwjc": "bad"}, {"code": "000002"}, None]
    ))
    result = catalog.search("", limit=1)
    assert result["total"] == 3 and len(result["items"]) == 1
    assert result["items"][0]["code"] == "000001"
    assert json.loads(catalog.cache_path.read_text())["items"][0]["code"] == "000001"


def test_public_endpoint_has_bounds_and_does_not_require_session(catalog, monkeypatch):
    monkeypatch.setattr(main, "stock_catalog", catalog)
    monkeypatch.setenv("AUTH_MODE", "supabase")
    with TestClient(main.app) as client:
        assert client.get("/stocks", params={"q": "gzmt"}).json()["items"][0]["code"] == "600519"
        assert client.get("/stocks", params={"limit": 5000}).status_code == 422
        assert client.get("/stocks", params={"q": "x" * 65}).status_code == 422
