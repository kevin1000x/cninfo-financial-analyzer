-- Server-side cache for AKShare annual data used by TNI calculations.
-- This table is intentionally inaccessible to browser clients.
create table public.financial_metrics (
    stock_code text not null check (stock_code ~ '^[0-9]{6}$'),
    year smallint not null check (year between 1990 and 2100),
    roa double precision,
    ocf double precision,
    source text not null default 'akshare' check (source = 'akshare'),
    fetched_at timestamptz not null default now(),
    primary key (stock_code, year)
);

alter table public.financial_metrics enable row level security;

revoke all on table public.financial_metrics from anon, authenticated;
grant select, insert, update, delete on table public.financial_metrics to service_role;

-- This project uses an event trigger to enable RLS on new public tables.
-- It must run with elevated privileges, but it is not an RPC endpoint.
do $$
begin
    if to_regprocedure('public.rls_auto_enable()') is not null then
        execute 'revoke execute on function public.rls_auto_enable() from public, anon, authenticated';
    end if;
end
$$;
