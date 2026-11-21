-- rls_auto_enable() is an event-trigger function (SECURITY DEFINER). Event triggers fire
-- regardless of EXECUTE grants, so API roles never need to call it directly.
revoke execute on function public.rls_auto_enable() from public, anon, authenticated;
