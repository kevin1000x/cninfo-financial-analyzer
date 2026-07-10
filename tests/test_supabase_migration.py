from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "supabase/migrations/20260709191043_financial_metrics_cache.sql"
)


def test_financial_metrics_migration_revokes_public_execution_of_rls_trigger():
    migration = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "to_regprocedure('public.rls_auto_enable()')" in migration
    assert (
        "execute 'revoke execute on function public.rls_auto_enable() "
        "from public, anon, authenticated'"
    ) in migration.lower()
