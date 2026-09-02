#!/usr/bin/env python
"""Annual-report readability vs. current-year performance (FINDING-01).

Research question
-----------------
Do firms with worse current-year performance write less readable annual
reports?  This is the "obfuscation" question, applied to A-share filings.

Design notes that matter for reading the result
-----------------------------------------------
* Sampling frame is the **Shanghai main board** (600/601/603) taken from
  CNINFO's own stock list.  Other boards are excluded on purpose: their
  filing templates differ, and template differences would show up as
  readability differences that have nothing to do with performance.
* The sample is drawn with a **fixed seed**, so `sample` is reproducible.
* Readability is the Chinese-adapted Gunning-Fog implemented in
  `src/text_analyzer.py`; it is computed on the **MD&A section only**, not
  the whole filing, because the financial statements are boilerplate tables.
* Significance uses a **normal approximation** (`statistics.NormalDist`).
  At n~200 the difference from Student-t is in the third decimal; this is
  stated here rather than hidden so the reader can discount it.

Stages (each is resumable; run them in order)
---------------------------------------------
    python scripts/readability_vs_performance.py sample
    python scripts/readability_vs_performance.py fetch
    python scripts/readability_vs_performance.py fog
    python scripts/readability_vs_performance.py roa
    python scripts/readability_vs_performance.py analyze

PDFs land in `data/raw/rvp/`, which .gitignore covers.  They must never
enter version control.  The manifest records SHA-256 + size instead.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

import requests
import urllib3
import yaml

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

OUT = REPO / "data" / "results"
PDF_DIR = REPO / "data" / "raw" / "rvp"
SAMPLE_CSV = OUT / "rvp_sample.csv"
MANIFEST = OUT / "rvp_manifest.json"
FOG_CSV = OUT / "rvp_fog.csv"
FOG_CACHE = OUT / "rvp_fog_cache.json"
ROA_CSV = OUT / "rvp_roa.csv"
RESULT = OUT / "rvp_result.json"

STOCK_LIST_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_HOST = "http://static.cninfo.com.cn/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

#: Shanghai main board only -- see module docstring.
PREFIXES = ("600", "601", "603")
SEED = 20260902
DEFAULT_N = 200
DEFAULT_YEAR = 2024
#: Human-paced; we are a guest on someone else's server.
SLEEP = 1.5

#: An MD&A shorter than this is almost certainly a table-of-contents hit,
#: which the README lists as a known failure mode.  Mark it, never score it.
MIN_MDA_CHARS = 2000


# ---------------------------------------------------------------- utilities
def _sleep() -> None:
    time.sleep(SLEEP)


def _load_config() -> dict:
    with open(REPO / "config.yaml", "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _rows(path: Path) -> list:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _write(path: Path, rows: list, fields: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print("wrote {}  ({} rows)".format(path.relative_to(REPO), len(rows)))


# ------------------------------------------------------------------ sample
def stage_sample(n: int, year: int) -> None:
    resp = requests.get(STOCK_LIST_URL, headers=UA, timeout=60, verify=False)
    resp.raise_for_status()
    frame = [
        e for e in (resp.json().get("stockList") or [])
        if str(e.get("code", "")).startswith(PREFIXES) and e.get("orgId")
    ]
    print("sampling frame: {} companies (Shanghai main board)".format(len(frame)))
    if len(frame) < n:
        raise SystemExit("frame smaller than requested sample")

    frame.sort(key=lambda e: e["code"])          # deterministic order first
    rng = random.Random(SEED)                    # then a fixed-seed draw
    picked = sorted(rng.sample(frame, n), key=lambda e: e["code"])
    _write(
        SAMPLE_CSV,
        [{"stock_code": e["code"], "org_id": e["orgId"],
          "name": e.get("zwjc", ""), "year": year} for e in picked],
        ["stock_code", "org_id", "name", "year"],
    )


# ------------------------------------------------------------------- fetch
def _find_report(code: str, org: str, year: int):
    """Return the announcement dict for the full annual report, or None.

    Three announcements share the year: the report, an English version and a
    summary.  Taking the first hit would silently mix them, and a summary's
    Fog index is not the report's Fog index.
    """
    params = {
        "pageNum": 1, "pageSize": 30, "column": "sse", "tabName": "fulltext",
        "stock": "{},{}".format(code, org), "searchkey": "", "secid": "",
        "plate": "", "category": "category_ndbg_szsh", "trade": "",
        "seDate": "{}-01-01~{}-12-31".format(year + 1, year + 1),
        "isHLtitle": "true",
    }
    resp = requests.post(QUERY_URL, data=params, headers=UA, timeout=30, verify=False)
    resp.raise_for_status()
    hits = []
    for ann in (resp.json().get("announcements") or []):
        title = ann.get("announcementTitle") or ""
        if str(year) not in title or "年度报告" not in title:
            continue
        if any(bad in title for bad in ("摘要", "英文",
                                        "更正", "补充")):
            continue
        hits.append(ann)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    # More than one surviving hit means the filter is too loose for this
    # filing.  Report it rather than picking one -- picking one would be a
    # silent choice with no basis behind it.
    return {"_ambiguous": [h.get("announcementTitle") for h in hits]}


def stage_fetch(year: int) -> None:
    rows = _rows(SAMPLE_CSV)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    for i, row in enumerate(rows, 1):
        code = row["stock_code"]
        if manifest.get(code, {}).get("status") == "ok":
            continue
        try:
            ann = _find_report(code, row["org_id"], year)
            _sleep()
            if ann is None:
                manifest[code] = {"status": "no_match"}
            elif "_ambiguous" in ann:
                manifest[code] = {"status": "ambiguous", "titles": ann["_ambiguous"]}
            else:
                dest = PDF_DIR / "{}_{}.pdf".format(code, year)
                if not dest.exists():
                    url = STATIC_HOST + ann["adjunctUrl"]
                    pdf = requests.get(url, headers=UA, timeout=180, verify=False)
                    pdf.raise_for_status()
                    dest.write_bytes(pdf.content)
                    _sleep()
                blob = dest.read_bytes()
                manifest[code] = {
                    "status": "ok",
                    "title": ann.get("announcementTitle"),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "bytes": len(blob),
                }
        except Exception as exc:                       # noqa: BLE001
            # Record the failure instead of dropping the company: the failure
            # distribution is part of the result.
            manifest[code] = {"status": "error",
                              "error": "{}: {}".format(type(exc).__name__, exc)}
        if i % 10 == 0 or i == len(rows):
            MANIFEST.parent.mkdir(parents=True, exist_ok=True)
            MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                encoding="utf-8")
            ok = sum(1 for v in manifest.values() if v.get("status") == "ok")
            print("  {}/{}  ok={}".format(i, len(rows), ok))

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    tally = {}
    for value in manifest.values():
        key = value.get("status", "?")
        tally[key] = tally.get(key, 0) + 1
    print("fetch tally:", tally)


# --------------------------------------------------------------------- fog
def stage_fog(year: int) -> None:
    from src.pdf_parser import PDFParser
    from src.text_analyzer import TextAnalyzer

    cfg = _load_config()
    parser = PDFParser(cfg)
    analyzer = TextAnalyzer(cfg, cfg["analyzer"]["sentiment_dict_path"])

    # Resumable: one filing takes 15-30s to parse, so a 200-company run is
    # over an hour.  Losing that to one crash near the end is not acceptable,
    # and `no_pdf` is deliberately NOT cached -- a filing that arrives later
    # must still be picked up on the next run.
    cache = {}
    if FOG_CACHE.exists():
        cache = json.loads(FOG_CACHE.read_text(encoding="utf-8"))

    out = []
    for i, row in enumerate(_rows(SAMPLE_CSV), 1):
        code = row["stock_code"]
        pdf = PDF_DIR / "{}_{}.pdf".format(code, year)
        if code in cache and pdf.exists():
            out.append(cache[code])
            continue
        if not pdf.exists():
            out.append({"stock_code": code, "status": "no_pdf",
                        "fog_index": "", "mda_chars": ""})
        else:
            try:
                text = parser.extract_text(str(pdf))
                mda = parser.extract_mda_section(text) or ""
                # `extract_mda_section` falls through to `return text` when no
                # candidate validates.  That fallback is a whole annual report:
                # it sails past any length check and would then be scored as if
                # it were the MD&A -- a silent wrong answer, and the only kind
                # of failure this study cannot detect after the fact.
                if text and len(mda) > 0.5 * len(text):
                    out.append({"stock_code": code, "status": "mda_fallback_fulltext",
                                "fog_index": "", "mda_chars": len(mda)})
                elif len(mda) < MIN_MDA_CHARS:
                    out.append({"stock_code": code, "status": "mda_too_short",
                                "fog_index": "", "mda_chars": len(mda)})
                else:
                    fog = analyzer.calculate_fog_index(mda)
                    out.append({"stock_code": code, "status": "ok",
                                "fog_index": "{:.4f}".format(fog["fog_index"]),
                                "mda_chars": len(mda)})
            except Exception as exc:                   # noqa: BLE001
                out.append({"stock_code": code,
                            "status": "error:" + type(exc).__name__,
                            "fog_index": "", "mda_chars": ""})
            cache[code] = out[-1]
            if i % 5 == 0:
                FOG_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        print("  {} -> {} {}".format(code, out[-1]["status"], out[-1]["fog_index"]))
    FOG_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    _write(FOG_CSV, out, ["stock_code", "status", "fog_index", "mda_chars"])


# --------------------------------------------------------------------- roa
def stage_roa(year: int) -> None:
    """Pull ROA from AKShare -- the repo's existing financial data source.

    The denominator convention (period-end vs. average total assets) is
    AKShare's, not ours, and it is not documented there.  That is a real
    limitation and belongs in FINDING-01's limitations section; it is not a
    reason to hand-roll a different ROA, which would make the number
    unreproducible from the repo's own commands.
    """
    import akshare

    from src.financial_data_sources import AKShareFinancialProvider

    # Record the version that actually produced the numbers.  requirements.txt
    # pins 1.18.64; if the installed version differs, the pin is what a reader
    # following the README would get, so the gap has to be visible.
    version = getattr(akshare, "__version__", "unknown")
    (OUT / "rvp_roa_provenance.json").write_text(
        json.dumps({"akshare_version": version, "year": year},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print("akshare version in use:", version)

    codes = [r["stock_code"] for r in _rows(SAMPLE_CSV)]
    # One AKShare call per company; the provider handles the schema mapping.
    provider = AKShareFinancialProvider()
    frame = provider.fetch(codes, [year])
    out = []
    for code in codes:
        sub = frame[(frame["stock_code"] == code) & (frame["year"] == year)]
        if sub.empty or sub.iloc[0]["roa"] is None:
            out.append({"stock_code": code, "status": "missing", "roa": "", "ocf": ""})
        else:
            row = sub.iloc[0]
            out.append({"stock_code": code, "status": "ok",
                        "roa": row["roa"], "ocf": row["ocf"]})
    _write(ROA_CSV, out, ["stock_code", "status", "roa", "ocf"])


# ----------------------------------------------------------------- analyze
def _pearson(xs: list, ys: list):
    """Pearson r plus a two-sided p-value under a normal approximation."""
    n = len(xs)
    if n < 4:
        return float("nan"), float("nan")
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return float("nan"), float("nan")
    r = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)
    r = max(-0.999999, min(0.999999, r))
    t = r * math.sqrt((n - 2) / (1 - r * r))
    p = 2 * (1 - statistics.NormalDist().cdf(abs(t)))
    return r, p


def stage_analyze(year: int) -> None:
    fog = {r["stock_code"]: r for r in _rows(FOG_CSV)}
    roa = {r["stock_code"]: r for r in _rows(ROA_CSV)}
    pairs = []
    for code, f in fog.items():
        g = roa.get(code)
        if f["status"] != "ok" or not g or g["status"] != "ok":
            continue
        try:
            pairs.append((code, float(f["fog_index"]), float(g["roa"])))
        except ValueError:
            continue

    n = len(pairs)
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    if n < 4:
        print("usable pairs = {}; too few to report anything. Stopping.".format(n))
        RESULT.write_text(json.dumps({"n": n, "verdict": "insufficient_data"},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
        return

    fogs = [p[1] for p in pairs]
    roas = [p[2] for p in pairs]
    r, p = _pearson(fogs, roas)
    loss = [f for _, f, a in pairs if a < 0]
    profit = [f for _, f, a in pairs if a >= 0]

    res = {
        "year": year,
        "n_usable": n,
        "fog_mean": round(statistics.fmean(fogs), 4),
        "fog_sd": round(statistics.pstdev(fogs), 4),
        "pearson_r_fog_vs_roa": round(r, 4),
        "p_value_normal_approx": round(p, 5),
        "n_loss": len(loss),
        "n_profit": len(profit),
        "fog_mean_loss": round(statistics.fmean(loss), 4) if loss else None,
        "fog_mean_profit": round(statistics.fmean(profit), 4) if profit else None,
        "note": ("p-value uses a normal approximation (statistics.NormalDist), "
                 "not Student-t; at this n the difference is in the third decimal."),
    }
    RESULT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print("\nA null result is a result. Do not re-run with a different model "
          "until p drops.")


# -------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="readability vs performance")
    ap.add_argument("stage", choices=["sample", "fetch", "fog", "roa", "analyze"])
    ap.add_argument("--n", type=int, default=DEFAULT_N)
    ap.add_argument("--year", type=int, default=DEFAULT_YEAR)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stages = {
        "sample": lambda: stage_sample(args.n, args.year),
        "fetch": lambda: stage_fetch(args.year),
        "fog": lambda: stage_fog(args.year),
        "roa": lambda: stage_roa(args.year),
        "analyze": lambda: stage_analyze(args.year),
    }
    stages[args.stage]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
