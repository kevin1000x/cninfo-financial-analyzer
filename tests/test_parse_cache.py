"""
Unit tests for src/parse_cache.py (disk layer + Supabase layer).
"""

from __future__ import annotations

import base64
import os

from src.parse_cache import (
    DiskParseCache,
    ParseCache,
    SupabaseParsedReportStore,
    _gzip_json,
    cache_key_for_announcement,
)


ANN = {"adjunctUrl": "finalpage/2021-04-30/12345.PDF",
       "announcementTitle": "2020年年度报告"}


# ------------------------------------------------------------------ keys

def test_cache_key_stable_and_versioned():
    a = cache_key_for_announcement(ANN)
    b = cache_key_for_announcement(dict(ANN))
    assert a == b
    assert a.startswith("v1_")
    assert len(a) == len("v1_") + 32


def test_cache_key_empty_without_adjunct():
    assert cache_key_for_announcement({}) == ""
    assert cache_key_for_announcement({"adjunctUrl": "  "}) == ""


# ------------------------------------------------------------------ disk

def test_disk_roundtrip(tmp_path):
    cache = DiskParseCache(root=str(tmp_path / "c"))
    cache.store("k1", {"text": "正文", "mda_text": "经营讨论", "file_size_bytes": 123})
    assert cache.load("k1") == {
        "text": "正文", "mda_text": "经营讨论", "file_size_bytes": 123
    }
    assert (tmp_path / "c" / "k1.json.gz").exists()


def test_disk_load_missing_returns_none(tmp_path):
    cache = DiskParseCache(root=str(tmp_path / "c"))
    assert cache.load("nope") is None


def test_disk_corrupt_entry_removed(tmp_path):
    root = tmp_path / "c"
    root.mkdir()
    (root / "bad.json.gz").write_bytes(b"not-gzip")
    cache = DiskParseCache(root=str(root))
    assert cache.load("bad") is None
    assert not (root / "bad.json.gz").exists()


def test_disk_prune_keeps_newest(tmp_path):
    cache = DiskParseCache(root=str(tmp_path / "c"), max_entries=2)
    for i, key in enumerate(["a", "b", "c"]):
        cache.store(key, {"text": str(i)})
        os.utime(tmp_path / "c" / f"{key}.json.gz", (1000 + i, 1000 + i))
    remaining = sorted(p.name for p in (tmp_path / "c").glob("*.json.gz"))
    assert remaining == ["b.json.gz", "c.json.gz"]


# ------------------------------------------------------------- supabase

class _FakeQuery:
    def __init__(self, rows, upserts):
        self._rows = rows
        self._upserts = upserts

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def limit(self, *_):
        return self

    def upsert(self, payload, on_conflict=None):
        self._upserts.append(payload)
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows
        self.upserts = []

    def table(self, _name):
        return _FakeQuery(self._rows, self.upserts)


def test_supabase_store_loads_base64_text_payload():
    blob = _gzip_json({"text": "t"})
    client = _FakeClient([{"payload": base64.b64encode(blob).decode("ascii")}])
    store = SupabaseParsedReportStore(client)
    assert store.load("k") == blob
    store.store("k2", blob)
    # upsert payload must be base64 text (PostgREST/JSON cannot carry bytes)
    assert client.upserts[-1]["payload"] == base64.b64encode(blob).decode("ascii")
    assert isinstance(client.upserts[-1]["payload"], str)


def test_supabase_store_loads_raw_bytes_payload_defensively():
    blob = _gzip_json({"text": "t"})
    store = SupabaseParsedReportStore(_FakeClient([{"payload": blob}]))
    assert store.load("k") == blob


def test_supabase_store_empty_table_returns_none():
    store = SupabaseParsedReportStore(_FakeClient([]))
    assert store.load("k") is None


def test_supabase_store_from_env_none_without_credentials(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert SupabaseParsedReportStore.from_env() is None


# ---------------------------------------------------------- ParseCache

def test_parse_cache_disk_miss_remote_hit_backfills_disk(tmp_path):
    payload = {"text": "正文", "mda_text": "MD&A", "file_size_bytes": 9}

    class Remote:
        def load(self, key):
            return _gzip_json(payload)

        def store(self, key, blob):
            raise AssertionError("put() should not be called during get()")

    cache = ParseCache(disk=DiskParseCache(root=str(tmp_path / "c")), remote=Remote())
    assert cache.get("k") == payload
    # backfilled to disk: a fresh disk-only cache now sees it
    disk_only = ParseCache(disk=DiskParseCache(root=str(tmp_path / "c")), remote=None)
    assert disk_only.get("k") == payload


def test_parse_cache_remote_error_is_swallowed(tmp_path):
    class Remote:
        def load(self, key):
            raise RuntimeError("table missing")

    cache = ParseCache(disk=DiskParseCache(root=str(tmp_path / "c")), remote=Remote())
    assert cache.get("k") is None  # no exception


def test_parse_cache_put_remote_error_is_swallowed(tmp_path):
    disk = DiskParseCache(root=str(tmp_path / "c"))

    class Remote:
        def store(self, key, blob):
            raise RuntimeError("table missing")

    cache = ParseCache(disk=disk, remote=Remote())
    cache.put("k", text="t", mda_text="m", file_size_bytes=1)
    assert disk.load("k")["text"] == "t"  # disk layer still written


def test_parse_cache_disabled_is_noop(tmp_path):
    cache = ParseCache(disk=None, remote=None)
    assert cache.enabled is False
    cache.put("k", text="t", mda_text="m", file_size_bytes=1)
    assert cache.get("k") is None


def test_from_env_off_disables_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("PARSE_CACHE", "off")
    monkeypatch.chdir(tmp_path)
    cache = ParseCache.from_env()
    assert cache.enabled is False
    assert cache.disk is None and cache.remote is None


def test_from_env_default_enables_disk(monkeypatch, tmp_path):
    monkeypatch.delenv("PARSE_CACHE", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    cache = ParseCache.from_env()
    assert cache.enabled is True
    assert cache.disk is not None
    assert cache.remote is None


def test_get_and_put_ignore_empty_keys(tmp_path):
    cache = ParseCache(disk=DiskParseCache(root=str(tmp_path / "c")), remote=None)
    assert cache.get("") is None
    cache.put("", text="t", mda_text="m", file_size_bytes=1)  # no crash
    cache.put("k", text="", mda_text="", file_size_bytes=0)  # empty text → skip
    assert cache.get("k") is None
