"""
Parse-result cache for the streaming pipeline.

pdfplumber parsing is the single largest CPU cost of a job on the
cpu-basic HF Space, and the same announcement PDF is re-parsed on every
run. This module caches the parse output keyed by the announcement's
adjunctUrl (stable per published PDF), so repeat analyses of the same
report skip BOTH the PDF download and the parse.

Two layers, both optional and failure-tolerant (cache problems must
never fail a job):
  disk      — gzipped JSON under data/parsed_cache/, pruned to a cap;
              survives between jobs but not Space rebuilds (ephemeral FS)
  supabase  — payload bytea in the parsed_reports table (service_role
              only, migration supabase/migrations/*_parsed_reports_cache.sql);
              durable across restarts once the DDL is applied

Keys are versioned (PARSE_CACHE_VERSION) so changing the parser or the
payload schema invalidates stale entries wholesale.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


PARSE_CACHE_VERSION = "v1"
DEFAULT_DISK_DIR = "data/parsed_cache"
DEFAULT_MAX_ENTRIES = 100


def cache_key_for_announcement(announcement: Dict[str, Any]) -> str:
    """Stable cache key for an announcement; '' when unusable."""
    adjunct_url = str(announcement.get("adjunctUrl", "")).strip()
    if not adjunct_url:
        return ""
    digest = hashlib.md5(adjunct_url.encode("utf-8")).hexdigest()
    return f"{PARSE_CACHE_VERSION}_{digest}"


def _gzip_json(payload: Dict[str, Any]) -> bytes:
    return gzip.compress(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )


def _gunzip_json(blob: bytes) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(gzip.decompress(blob).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


class DiskParseCache:
    """Gzipped-JSON files under a dedicated directory, capped by mtime."""

    def __init__(self, root: str = DEFAULT_DISK_DIR,
                 max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.root = Path(root)
        self.max_entries = max(0, int(max_entries))

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json.gz"

    def load(self, key: str) -> Optional[Dict[str, Any]]:
        path = self._path(key)
        if not path.exists():
            return None
        data = _gunzip_json(path.read_bytes())
        if data is None:
            logger.warning(f"Corrupt parse cache entry removed: {path}")
            path.unlink(missing_ok=True)
            return None
        return data

    def store(self, key: str, payload: Dict[str, Any]) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._path(key).write_bytes(_gzip_json(payload))
            self._prune()
        except OSError as exc:
            logger.warning(f"Parse cache disk write failed: {exc}")

    def _prune(self) -> None:
        if self.max_entries <= 0:
            return
        try:
            entries = sorted(
                self.root.glob("*.json.gz"),
                key=lambda p: (p.stat().st_mtime, p.name),
            )
        except OSError:
            return
        while len(entries) > self.max_entries:
            oldest = entries.pop(0)
            oldest.unlink(missing_ok=True)


class SupabaseParsedReportStore:
    """parsed_reports table adapter; only trusted backend processes use it."""

    def __init__(self, client) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> Optional["SupabaseParsedReportStore"]:
        """Reuse the financial-cache credentials; None when not configured.

        A missing table surfaces as an APIError on first use — callers log
        a warning and keep serving from the disk layer, so the DDL does
        not have to be applied for the feature to work."""
        url = os.environ.get("SUPABASE_URL", "").strip()
        service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not service_role_key:
            return None
        try:
            from supabase import create_client
        except ImportError:
            return None
        return cls(create_client(url, service_role_key))

    def load(self, key: str) -> Optional[bytes]:
        response = (
            self._client.table("parsed_reports")
            .select("payload")
            .eq("cache_key", key)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        payload = rows[0].get("payload")
        if payload is None:
            return None
        if isinstance(payload, str):
            # payload column is base64 text (PostgREST/JSON cannot round-trip
            # raw bytea through the REST interface).
            return base64.b64decode(payload)
        return bytes(payload)

    def store(self, key: str, blob: bytes) -> None:
        self._client.table("parsed_reports").upsert(
            {"cache_key": key, "payload": base64.b64encode(blob).decode("ascii")},
            on_conflict="cache_key",
        ).execute()


class ParseCache:
    """Two-layer lookup: disk first (fast, local), then Supabase (durable).

    enabled=False turns every operation into a no-op. Supabase errors are
    logged once per call site and swallowed — the pipeline runs fine with
    the disk layer alone."""

    def __init__(self,
                 disk: Optional[DiskParseCache] = None,
                 remote: Optional[SupabaseParsedReportStore] = None) -> None:
        self.disk = disk
        self.remote = remote
        self.enabled = disk is not None or remote is not None

    @classmethod
    def from_env(cls) -> "ParseCache":
        if os.environ.get("PARSE_CACHE", "").strip().lower() == "off":
            return cls(disk=None, remote=None)
        return cls(
            disk=DiskParseCache(),
            remote=SupabaseParsedReportStore.from_env(),
        )

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Return {'text', 'mda_text', 'file_size_bytes'} or None."""
        if not self.enabled or not key:
            return None

        if self.disk is not None:
            data = self.disk.load(key)
            if data is not None:
                return data

        if self.remote is not None:
            try:
                blob = self.remote.load(key)
            except Exception as exc:
                logger.warning(f"Parse cache remote read failed: {exc}")
                blob = None
            if blob is not None:
                data = _gunzip_json(blob)
                if data is not None:
                    if self.disk is not None:
                        self.disk.store(key, data)  # backfill local layer
                    return data

        return None

    def put(self, key: str, *,
            text: str, mda_text: str, file_size_bytes: int) -> None:
        if not self.enabled or not key or not text:
            return
        payload = {
            "text": text,
            "mda_text": mda_text,
            "file_size_bytes": int(file_size_bytes),
        }
        if self.disk is not None:
            self.disk.store(key, payload)
        if self.remote is not None:
            try:
                self.remote.store(key, _gzip_json(payload))
            except Exception as exc:
                logger.warning(f"Unable to refresh parse cache: {exc}")
