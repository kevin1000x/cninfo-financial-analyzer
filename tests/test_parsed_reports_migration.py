from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "supabase/migrations/20260906120000_parsed_reports_cache.sql"
)


def test_parsed_reports_migration_locks_table_to_service_role():
    assert MIGRATION_PATH.is_file()
    migration = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "create table public.parsed_reports" in migration
    assert "payload text not null" in migration
    assert "alter table public.parsed_reports enable row level security" in migration
    assert (
        "revoke all on table public.parsed_reports from anon, authenticated"
        in migration
    )
    assert (
        "grant select, insert, update, delete on table public.parsed_reports "
        "to service_role" in migration
    )
