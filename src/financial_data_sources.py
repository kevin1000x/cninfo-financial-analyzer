"""Server-side financial-metric sources for TNI calculation.

AKShare supplies annual A-share financial indicators. Supabase is an
optional, server-only cache; the browser never receives its credentials.
"""

from __future__ import annotations

import os
from typing import Callable, Optional, Protocol, Sequence

import pandas as pd
from loguru import logger


METRIC_COLUMNS = ["stock_code", "year", "roa", "ocf"]
ROA_COLUMN_CANDIDATES = ("总资产净利润率(%)", "总资产利润率(%)", "资产报酬率(%)")
OCF_COLUMN_CANDIDATES = ("经营现金净流量(元)", "经营活动产生的现金流量净额")


class FinancialMetricsStore(Protocol):
    def load_metrics(
        self, company_codes: Sequence[str], years: Sequence[int]
    ) -> pd.DataFrame: ...

    def upsert_metrics(self, metrics: pd.DataFrame) -> None: ...


class FinancialMetricsSource(Protocol):
    def fetch(
        self, company_codes: Sequence[str], years: Sequence[int]
    ) -> pd.DataFrame: ...


class NullFinancialMetricsStore:
    """Local fallback when Supabase credentials are intentionally absent."""

    def load_metrics(
        self, company_codes: Sequence[str], years: Sequence[int]
    ) -> pd.DataFrame:
        return empty_metrics()

    def upsert_metrics(self, metrics: pd.DataFrame) -> None:
        return None


def empty_metrics() -> pd.DataFrame:
    return pd.DataFrame(columns=METRIC_COLUMNS)


def normalize_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    """Normalize metric keys and return one row per company fiscal year."""
    if metrics is None or metrics.empty:
        return empty_metrics()

    normalized = metrics.reindex(columns=METRIC_COLUMNS).copy()
    normalized["stock_code"] = normalized["stock_code"].map(
        lambda value: str(value).strip().zfill(6)
    )
    normalized["year"] = pd.to_numeric(normalized["year"], errors="coerce")
    normalized = normalized.dropna(subset=["stock_code", "year"])
    normalized["year"] = normalized["year"].astype(int)
    normalized["roa"] = pd.to_numeric(normalized["roa"], errors="coerce")
    normalized["ocf"] = pd.to_numeric(normalized["ocf"], errors="coerce")
    return (
        normalized.drop_duplicates(["stock_code", "year"], keep="last")
        .sort_values(["stock_code", "year"])
        .reset_index(drop=True)
    )


class AKShareFinancialProvider:
    """Adapt AKShare's Sina annual-indicator endpoint to the TNI schema."""

    def __init__(
        self,
        fetch_frame: Optional[Callable[..., pd.DataFrame]] = None,
        fetch_abstract: Optional[Callable[..., pd.DataFrame]] = None,
    ) -> None:
        self._fetch_frame = fetch_frame or self._default_fetch_frame
        self._fetch_abstract = fetch_abstract or self._default_fetch_abstract

    @staticmethod
    def _default_fetch_frame(*, symbol: str, start_year: str) -> pd.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:  # pragma: no cover - covered by packaging
            raise RuntimeError("AKShare is required for financial_data_source=akshare") from exc
        return ak.stock_financial_analysis_indicator(
            symbol=symbol,
            start_year=start_year,
        )

    @staticmethod
    def _default_fetch_abstract(*, symbol: str) -> pd.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:  # pragma: no cover - covered by packaging
            raise RuntimeError("AKShare is required for financial_data_source=akshare") from exc
        return ak.stock_financial_abstract(symbol=symbol)

    @staticmethod
    def _first_column(frame: pd.DataFrame, candidates: Sequence[str]) -> pd.Series:
        for name in candidates:
            if name in frame.columns:
                return pd.to_numeric(frame[name], errors="coerce")
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")

    @staticmethod
    def _abstract_operating_cash_flow(
        abstract: pd.DataFrame, stock_code: str, years: Sequence[int]
    ) -> pd.DataFrame:
        if abstract is None or abstract.empty or "指标" not in abstract.columns:
            return pd.DataFrame(columns=["stock_code", "year", "ocf"])

        rows = abstract.loc[
            abstract["指标"].astype(str).str.strip().eq("经营现金流量净额")
        ]
        if rows.empty:
            return pd.DataFrame(columns=["stock_code", "year", "ocf"])

        cash_flow_row = rows.iloc[0]
        records = []
        for year in years:
            value_column = f"{year}1231"
            if value_column not in cash_flow_row.index:
                continue
            value = pd.to_numeric(
                pd.Series([cash_flow_row[value_column]]), errors="coerce"
            ).iloc[0]
            if pd.notna(value):
                records.append(
                    {"stock_code": stock_code, "year": year, "ocf": float(value)}
                )
        return pd.DataFrame(records, columns=["stock_code", "year", "ocf"])

    def fetch(
        self, company_codes: Sequence[str], years: Sequence[int]
    ) -> pd.DataFrame:
        requested_years = sorted({int(year) for year in years})
        if not company_codes or not requested_years:
            return empty_metrics()

        frames: list[pd.DataFrame] = []
        for company_code in company_codes:
            stock_code = str(company_code).strip().zfill(6)
            raw = self._fetch_frame(symbol=stock_code, start_year=str(min(requested_years)))
            if raw is None or raw.empty or "日期" not in raw.columns:
                continue

            frame = raw.copy()
            report_date = pd.to_datetime(frame["日期"], errors="coerce")
            frame["year"] = report_date.dt.year
            frame = frame.loc[
                report_date.dt.strftime("%m-%d").eq("12-31")
                & frame["year"].isin(requested_years)
            ].copy()
            if frame.empty:
                continue

            frame["stock_code"] = stock_code
            frame["roa"] = self._first_column(frame, ROA_COLUMN_CANDIDATES)
            frame["ocf"] = self._first_column(frame, OCF_COLUMN_CANDIDATES)
            frames.append(frame[METRIC_COLUMNS])

        if not frames:
            return empty_metrics()

        metrics = normalize_metrics(pd.concat(frames, ignore_index=True))
        missing_ocf = metrics.loc[metrics["ocf"].isna(), ["stock_code", "year"]]
        if missing_ocf.empty:
            return metrics

        abstract_ocf_frames: list[pd.DataFrame] = []
        for stock_code in missing_ocf["stock_code"].unique():
            missing_years = missing_ocf.loc[
                missing_ocf["stock_code"].eq(stock_code), "year"
            ].tolist()
            try:
                abstract_ocf_frames.append(
                    self._abstract_operating_cash_flow(
                        self._fetch_abstract(symbol=stock_code), stock_code, missing_years
                    )
                )
            except Exception as exc:
                logger.warning(
                    f"Unable to load AKShare operating cash flow for {stock_code}: {exc}"
                )

        if not abstract_ocf_frames:
            return metrics
        abstract_ocf = pd.concat(abstract_ocf_frames, ignore_index=True)
        if abstract_ocf.empty:
            return metrics

        cash_flow_by_key = {
            (row.stock_code, row.year): row.ocf
            for row in abstract_ocf.itertuples(index=False)
        }
        for index, row in metrics.loc[metrics["ocf"].isna()].iterrows():
            value = cash_flow_by_key.get((row["stock_code"], row["year"]))
            if value is not None:
                metrics.at[index, "ocf"] = value
        return normalize_metrics(metrics)


class CachedFinancialDataProvider:
    """Read cached metrics first and fetch only companies with missing years."""

    def __init__(self, store: FinancialMetricsStore, source: FinancialMetricsSource) -> None:
        self._store = store
        self._source = source

    def load(
        self, company_codes: Sequence[str], years: Sequence[int]
    ) -> pd.DataFrame:
        requested_years = sorted({int(year) for year in years})
        if not company_codes or not requested_years:
            return empty_metrics()

        required_years = sorted({min(requested_years) - 1, *requested_years})
        normalized_codes = [str(code).strip().zfill(6) for code in company_codes]
        try:
            cached = normalize_metrics(
                self._store.load_metrics(normalized_codes, required_years)
            )
        except Exception as exc:
            logger.warning(f"Financial metric cache unavailable; using AKShare directly: {exc}")
            cached = empty_metrics()
        cached_pairs = set(zip(cached["stock_code"], cached["year"]))
        missing_codes = [
            code
            for code in normalized_codes
            if any((code, year) not in cached_pairs for year in required_years)
        ]

        fresh = empty_metrics()
        if missing_codes:
            fresh = normalize_metrics(self._source.fetch(missing_codes, required_years))
            if not fresh.empty:
                try:
                    self._store.upsert_metrics(fresh)
                except Exception as exc:
                    logger.warning(f"Unable to refresh financial metric cache: {exc}")

        return normalize_metrics(pd.concat([cached, fresh], ignore_index=True))


class SupabaseFinancialMetricsStore:
    """Supabase REST adapter used only by trusted backend processes."""

    def __init__(self, client) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> Optional["SupabaseFinancialMetricsStore"]:
        url = os.environ.get("SUPABASE_URL", "").strip()
        service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not service_role_key:
            return None

        try:
            from supabase import create_client
        except ImportError as exc:  # pragma: no cover - covered by packaging
            raise RuntimeError("supabase is required when Supabase credentials are configured") from exc
        return cls(create_client(url, service_role_key))

    def load_metrics(
        self, company_codes: Sequence[str], years: Sequence[int]
    ) -> pd.DataFrame:
        if not company_codes or not years:
            return empty_metrics()
        response = (
            self._client.table("financial_metrics")
            .select("stock_code,year,roa,ocf")
            .in_("stock_code", list(company_codes))
            .in_("year", list(years))
            .execute()
        )
        return normalize_metrics(pd.DataFrame(response.data or []))

    def upsert_metrics(self, metrics: pd.DataFrame) -> None:
        normalized = normalize_metrics(metrics)
        if normalized.empty:
            return
        payload = normalized.astype(object).where(pd.notna(normalized), None).to_dict("records")
        (
            self._client.table("financial_metrics")
            .upsert(payload, on_conflict="stock_code,year")
            .execute()
        )
