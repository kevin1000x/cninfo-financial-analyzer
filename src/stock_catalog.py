"""Cached CNINFO security directory for name/code/source-pinyin lookup.

This is a directory, not a promise that a report or audited evidence exists.
No model invents names, aliases or coverage. Search never calls CNINFO per keypress.
"""

from __future__ import annotations

import json
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


STOCK_LIST_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CACHE_TTL_SECONDS = 86400
RETRY_SECONDS = 60


class CatalogUnavailable(RuntimeError):
    pass


def _search_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _normalise(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("invalid stock list")
    found: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code", "")).strip()
        name = str(raw.get("zwjc") or raw.get("name") or "").strip()
        if len(code) != 6 or not code.isascii() or not code.isdigit() or not name:
            continue
        if code.startswith(("4", "8", "92")):
            market = "BJ"
        elif code.startswith(("6", "9")):
            market = "SH"
        elif code.startswith(("0", "2", "3")):
            market = "SZ"
        else:
            market = "UNKNOWN"
        found[code] = {
            "code": code,
            "name": name,
            "pinyin": str(raw.get("pinyin") or "").strip(),
            "market": market,
            "supported": market in {"SH", "SZ"},
        }
    if not found:
        raise ValueError("empty stock list")
    return sorted(found.values(), key=lambda row: row["code"])


class StockCatalog:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self._items: list[dict[str, Any]] = []
        self._updated = 0.0
        self._retry_after = 0.0
        self._lock = threading.Lock()
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self._items = _normalise(cache["items"])
            self._updated = float(cache["updated_at"])
        except (OSError, ValueError, KeyError, TypeError):
            self._items = []
            self._updated = 0.0

    def _refresh(self, now: float) -> None:
        self._retry_after = now + RETRY_SECONDS
        response = requests.get(
            STOCK_LIST_URL,
            headers={"User-Agent": "cninfo-workbench/1.0", "Referer": "https://www.cninfo.com.cn/"},
            timeout=8,
        )
        response.raise_for_status()
        items = _normalise(response.json().get("stockList"))
        self._items, self._updated = items, now
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.cache_path.with_suffix(".tmp")
            temp.write_text(json.dumps({"items": items, "updated_at": now}, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.cache_path)
        except OSError:
            # Read-only disks still benefit from the valid in-memory snapshot.
            pass

    def search(self, query: str, limit: int = 20) -> dict:
        with self._lock:
            self._load()
            now = time.time()
            if now - self._updated >= CACHE_TTL_SECONDS and now >= self._retry_after:
                try:
                    self._refresh(now)
                except (requests.RequestException, ValueError, AttributeError):
                    pass
            if not self._items:
                raise CatalogUnavailable("证券目录暂不可用，请稍后重试；仍可使用六位股票代码")
            needle = _search_key(query)
            matches = [row for row in self._items if any(
                needle in _search_key(row[field]) for field in ("code", "name", "pinyin")
            )]
            matches.sort(key=lambda row: (
                0 if needle in {_search_key(row["code"]), _search_key(row["name"])} else 1,
                0 if row["code"].startswith(needle) else 1,
                row["code"],
            ))
            return {
                "items": matches[:limit],
                "total": len(matches),
                "updated_at": datetime.fromtimestamp(self._updated, timezone.utc).isoformat(),
                "stale": now - self._updated >= CACHE_TTL_SECONDS,
                "source": "CNINFO",
                "notice": "证券目录仅供选择公司，不代表指定年份报告或核查证据已覆盖。",
            }
