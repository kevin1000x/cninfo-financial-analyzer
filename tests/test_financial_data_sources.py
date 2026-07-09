from __future__ import annotations

import pandas as pd


def test_akshare_provider_normalizes_annual_roa_and_operating_cash_flow():
    from src.financial_data_sources import AKShareFinancialProvider

    raw = pd.DataFrame(
        {
            "日期": ["2020-03-31", "2020-12-31", "2021-12-31"],
            "总资产净利润率(%)": [1.0, 2.5, 3.5],
            "经营现金净流量(元)": [100.0, 200.0, 300.0],
        }
    )
    provider = AKShareFinancialProvider(fetch_frame=lambda **_: raw)

    actual = provider.fetch(company_codes=["600000"], years=[2020, 2021])

    assert actual.to_dict("records") == [
        {"stock_code": "600000", "year": 2020, "roa": 2.5, "ocf": 200.0},
        {"stock_code": "600000", "year": 2021, "roa": 3.5, "ocf": 300.0},
    ]


def test_cached_provider_fetches_the_prior_year_needed_for_performance_change():
    from src.financial_data_sources import CachedFinancialDataProvider

    class Store:
        def load_metrics(self, company_codes, years):
            assert company_codes == ["600000"]
            assert years == [2020, 2021]
            return pd.DataFrame(
                [{"stock_code": "600000", "year": 2021, "roa": 3.5, "ocf": 300.0}]
            )

        def upsert_metrics(self, metrics):
            self.saved = metrics.copy()

    class Source:
        def __init__(self):
            self.calls = []

        def fetch(self, company_codes, years):
            self.calls.append((company_codes, years))
            return pd.DataFrame(
                [{"stock_code": "600000", "year": 2020, "roa": 2.5, "ocf": 200.0}]
            )

    store = Store()
    source = Source()
    provider = CachedFinancialDataProvider(store=store, source=source)

    actual = provider.load(company_codes=["600000"], years=[2021])

    assert source.calls == [(["600000"], [2020, 2021])]
    assert actual.to_dict("records") == [
        {"stock_code": "600000", "year": 2020, "roa": 2.5, "ocf": 200.0},
        {"stock_code": "600000", "year": 2021, "roa": 3.5, "ocf": 300.0},
    ]
    assert store.saved.to_dict("records") == [
        {"stock_code": "600000", "year": 2020, "roa": 2.5, "ocf": 200.0}
    ]


def test_supabase_store_is_disabled_without_server_credentials(monkeypatch):
    from src.financial_data_sources import SupabaseFinancialMetricsStore

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    assert SupabaseFinancialMetricsStore.from_env() is None


def test_cached_provider_falls_back_to_akshare_when_supabase_is_unavailable():
    from src.financial_data_sources import CachedFinancialDataProvider

    class FailingStore:
        def load_metrics(self, company_codes, years):
            raise RuntimeError("Supabase is paused")

        def upsert_metrics(self, metrics):
            raise RuntimeError("Supabase is paused")

    class Source:
        def fetch(self, company_codes, years):
            return pd.DataFrame(
                [{"stock_code": "600000", "year": 2020, "roa": 2.5, "ocf": 200.0}]
            )

    actual = CachedFinancialDataProvider(
        store=FailingStore(),
        source=Source(),
    ).load(company_codes=["600000"], years=[2020])

    assert actual.to_dict("records") == [
        {"stock_code": "600000", "year": 2020, "roa": 2.5, "ocf": 200.0}
    ]


def test_supabase_store_upserts_with_the_metric_primary_key():
    from src.financial_data_sources import SupabaseFinancialMetricsStore

    class Query:
        def __init__(self):
            self.payload = None
            self.on_conflict = None

        def upsert(self, payload, on_conflict):
            self.payload = payload
            self.on_conflict = on_conflict
            return self

        def execute(self):
            return None

    class Client:
        def __init__(self):
            self.query = Query()

        def table(self, name):
            assert name == "financial_metrics"
            return self.query

    client = Client()
    store = SupabaseFinancialMetricsStore(client=client)
    store.upsert_metrics(
        pd.DataFrame(
            [{"stock_code": "600000", "year": 2021, "roa": 3.5, "ocf": 300.0}]
        )
    )

    assert client.query.on_conflict == "stock_code,year"
    assert client.query.payload == [
        {"stock_code": "600000", "year": 2021, "roa": 3.5, "ocf": 300.0}
    ]
