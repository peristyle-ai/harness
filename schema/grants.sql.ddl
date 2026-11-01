\if :{?runtime_role}
\else
 \set runtime_role 'harness_app'
\endif
\if :{?operator_role}
\else
 \set operator_role 'harness_operator'
\endif
\if :{?agent_name}
\else
 DO $missing_agent_name$ BEGIN
 RAISE EXCEPTION 'agent_name is required (pass -v agent_name=<agent>)';
 END $missing_agent_name$;
\endif
GRANT USAGE ON SCHEMA :"agent_name" TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE
 ON ALL TABLES IN SCHEMA :"agent_name" TO :"runtime_role";
GRANT USAGE, SELECT
 ON ALL SEQUENCES IN SCHEMA :"agent_name" TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"operator_role" IN SCHEMA :"agent_name"
 GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"operator_role" IN SCHEMA :"agent_name"
 GRANT USAGE, SELECT ON SEQUENCES TO :"runtime_role";
GRANT USAGE ON SCHEMA public TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE ON public.users TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE ON public.app_settings TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.entity_icons TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.groups TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.group_members TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.group_agents TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.group_messages TO :"runtime_role";
GRANT USAGE, SELECT ON SEQUENCE public.users_id_seq TO :"runtime_role";
GRANT USAGE, SELECT ON SEQUENCE public.group_messages_id_seq TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"operator_role" IN SCHEMA public
 GRANT SELECT ON TABLES TO :"runtime_role";
SELECT CASE WHEN to_regclass('public.app_users') IS NOT NULL
 THEN 'true' ELSE 'false' END AS has_app_users \gset
\if :has_app_users
 GRANT SELECT ON public.app_users TO :"runtime_role";
\endif
