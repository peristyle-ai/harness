-- The harness's RUNTIME grants.
--
-- The schema (schema/init.sql) creates the objects; THIS file grants the
-- runtime role -- the role the app connects as -- the DML it needs, and
-- nothing more.
--
-- WHY IT IS A SEPARATE FILE
--   The runtime role's NAME is per-install (harness_app, peribot_app, ...),
--   so the grants cannot be baked into the schema: they are a psql
--   variable, exactly like the agent's schema name.
--
-- WHO RUNS IT
--   The OPERATOR role -- it owns the objects, and an owner can grant them.
--   The runtime role cannot (it has no ownership, and no CREATEROLE).
--
--   psql -v agent_name=<agent> \
--         -v runtime_role=<runtime> \
--         -v operator_role=<operator> \
--         -f schema/grants.sql
--
-- IDEMPOTENT: re-running it changes nothing.

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

-- ------------------------------------------------------------------ the agent's schema
GRANT USAGE ON SCHEMA :"agent_name" TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA :"agent_name" TO :"runtime_role";
GRANT USAGE, SELECT
    ON ALL SEQUENCES IN SCHEMA :"agent_name" TO :"runtime_role";

-- Future tables in this agent's schema get the same DML automatically.
ALTER DEFAULT PRIVILEGES FOR ROLE :"operator_role" IN SCHEMA :"agent_name"
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"operator_role" IN SCHEMA :"agent_name"
    GRANT USAGE, SELECT ON SEQUENCES TO :"runtime_role";

-- ------------------------------------------------------------------ the shared public tables
-- The app's login reads public.users; it also creates and edits users, and
-- owns the group-chat tables and the settings table.
--
-- NO DELETE on users or app_settings: deleting a user or a setting is not
-- something the app does.
GRANT USAGE ON SCHEMA public TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE ON public.users TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE ON public.app_settings TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.entity_icons TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.groups TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.group_members TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.group_agents TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.group_messages TO :"runtime_role";

-- The sequences behind those tables' ids.
GRANT USAGE, SELECT ON SEQUENCE public.users_id_seq TO :"runtime_role";
GRANT USAGE, SELECT ON SEQUENCE public.group_messages_id_seq TO :"runtime_role";

-- Future public tables: read-only for the runtime role.
ALTER DEFAULT PRIVILEGES FOR ROLE :"operator_role" IN SCHEMA public
    GRANT SELECT ON TABLES TO :"runtime_role";

-- A legacy alternate auth table: grant on it only when it exists.
SELECT CASE WHEN to_regclass('public.app_users') IS NOT NULL
            THEN 'true' ELSE 'false' END AS has_app_users \gset
\if :has_app_users
  GRANT SELECT ON public.app_users TO :"runtime_role";
\endif
