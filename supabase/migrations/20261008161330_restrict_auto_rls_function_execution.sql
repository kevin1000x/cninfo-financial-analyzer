-- Mirrors the existing cloud migration returned by Supabase list_migrations.
-- Automatic RLS remains active via the event trigger owned by postgres.
revoke execute on function public.rls_auto_enable() from public, anon, authenticated;
