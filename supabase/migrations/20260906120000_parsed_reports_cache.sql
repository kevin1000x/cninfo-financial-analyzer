-- Durable cache of parsed annual-report text, keyed by announcement
-- adjunctUrl hash (see src/parse_cache.py). Payload is a gzipped JSON
-- document {text, mda_text, file_size_bytes}.
-- This table is intentionally inaccessible to browser clients; the
-- backend reads and writes it with the service_role key only.
create table public.parsed_reports (
    cache_key text primary key,
    payload bytea not null,
    created_at timestamptz not null default now()
);

alter table public.parsed_reports enable row level security;

revoke all on table public.parsed_reports from anon, authenticated;
grant select, insert, update, delete on table public.parsed_reports to service_role;
