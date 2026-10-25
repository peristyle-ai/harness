-- The Peristyle Harness -- the complete schema, in one file.
--
-- Generated from the live database (the authoritative target state) by
-- scripts/build_init.py. It replaces the numbered migrations and the
-- per-agent schema files this project accreted while it was private: a
-- published product ships the schema it HAS, not the path it took to it.
--
-- WHAT IT CREATES
--   * the extensions: pgcrypto, pg_trgm, vector, citext
--   * the shared public tables the harness owns: users, app_settings,
--     entity_icons, groups, group_members, group_agents, group_messages
--   * the agent's schema (templated on :agent_name) with conversations,
--     memories, memory_audit, memory_chunks and turns, their indexes and
--     their constraints
--
-- HOW TO RUN IT
--   psql -d <db> -v agent_name=<agent> -f schema/init.sql
--
-- It is IDEMPOTENT: every object is created only if it is missing, and the
-- statements that would otherwise fail on a second run are guarded. Re-
-- running it against a live database changes nothing.
--
-- THE psql VARIABLE
-- psql substitutes a variable in ordinary SQL, but NOT inside a
-- dollar-quoted string and NOT inside a quoted literal. So the
-- schema-qualified table names use the :"agent_name" form, and the names
-- that must appear inside a string -- a regclass literal, a name used by a
-- DO block -- are built at run time from a session setting that this file
-- sets once, at the top.

\set ON_ERROR_STOP on

BEGIN;

-- The agent's schema name, as a session setting, so the DO blocks below
-- can build qualified names (psql will not interpolate inside them).
SELECT set_config('harness.agent_name', :'agent_name', false);

-- ---------------------------------------------------------------- extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "citext";

-- ============================================================================
-- The shared public schema
--
-- The agents' conversations reference public.users, and the harness's login
-- IS the users table -- so it is shared, not per-agent. It lives in public
-- because another project (the accounts service) may own it too; every
-- statement here is conditional, so it is safe to run either way.
-- ============================================================================

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'user_role' AND n.nspname = 'public') THEN
        CREATE TYPE public.user_role AS ENUM (
    'viewer',
    'tradesperson',
    'site_supervisor',
    'project_manager',
    'admin'
);
    END IF;
END
$guard$;

CREATE TABLE IF NOT EXISTS public.app_settings (
    key text NOT NULL,
    value jsonb DEFAULT 'false'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by bigint
);

CREATE TABLE IF NOT EXISTS public.entity_icons (
    entity_name text NOT NULL,
    filename text NOT NULL,
    mime text NOT NULL,
    size_bytes bigint NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by integer
);

CREATE TABLE IF NOT EXISTS public.group_agents (
    group_id uuid NOT NULL,
    agent_name text NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS public.group_members (
    group_id uuid NOT NULL,
    user_id bigint NOT NULL,
    role text DEFAULT 'member'::text NOT NULL,
    added_by bigint,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT group_members_role_check CHECK ((role = ANY (ARRAY['owner'::text, 'member'::text])))
);

CREATE TABLE IF NOT EXISTS public.group_messages (
    id bigint NOT NULL,
    group_id uuid NOT NULL,
    author_type text NOT NULL,
    author_id bigint,
    author_name text DEFAULT ''::text NOT NULL,
    content text NOT NULL,
    attachments jsonb DEFAULT '[]'::jsonb NOT NULL,
    mentions text[] DEFAULT '{}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT group_messages_author_type_check CHECK ((author_type = ANY (ARRAY['user'::text, 'agent'::text, 'system'::text])))
);

CREATE TABLE IF NOT EXISTS public.groups (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    conversation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    archived_at timestamp with time zone,
    CONSTRAINT groups_name_len CHECK (((char_length(btrim(name)) >= 1) AND (char_length(btrim(name)) <= 120)))
);

CREATE TABLE IF NOT EXISTS public.users (
    id bigint NOT NULL,
    email public.citext NOT NULL,
    password_hash text NOT NULL,
    display_name text DEFAULT ''::text NOT NULL,
    role text DEFAULT 'member'::text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    password_changed_at timestamp with time zone DEFAULT now() NOT NULL,
    role_scope jsonb DEFAULT '{}'::jsonb NOT NULL,
    is_local boolean DEFAULT true NOT NULL,
    CONSTRAINT users_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'member'::text, 'viewer'::text])))
);

CREATE SEQUENCE IF NOT EXISTS public.group_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE SEQUENCE IF NOT EXISTS public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.group_messages_id_seq OWNED BY public.group_messages.id;

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;

ALTER TABLE ONLY public.group_messages ALTER COLUMN id SET DEFAULT nextval('public.group_messages_id_seq'::regclass);

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);

CREATE INDEX IF NOT EXISTS group_members_user_idx ON public.group_members USING btree (user_id);

CREATE INDEX IF NOT EXISTS group_messages_group_idx ON public.group_messages USING btree (group_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx ON public.users USING btree (lower((email)::text));

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'app_settings_pkey' AND conrelid =
              'public.app_settings'::regclass) THEN
        ALTER TABLE public.app_settings ADD CONSTRAINT app_settings_pkey PRIMARY KEY (key);
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'entity_icons_pkey' AND conrelid =
              'public.entity_icons'::regclass) THEN
        ALTER TABLE public.entity_icons ADD CONSTRAINT entity_icons_pkey PRIMARY KEY (entity_name);
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'group_agents_pkey' AND conrelid =
              'public.group_agents'::regclass) THEN
        ALTER TABLE public.group_agents ADD CONSTRAINT group_agents_pkey PRIMARY KEY (group_id, agent_name);
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'group_members_pkey' AND conrelid =
              'public.group_members'::regclass) THEN
        ALTER TABLE public.group_members ADD CONSTRAINT group_members_pkey PRIMARY KEY (group_id, user_id);
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'group_messages_pkey' AND conrelid =
              'public.group_messages'::regclass) THEN
        ALTER TABLE public.group_messages ADD CONSTRAINT group_messages_pkey PRIMARY KEY (id);
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'groups_pkey' AND conrelid =
              'public.groups'::regclass) THEN
        ALTER TABLE public.groups ADD CONSTRAINT groups_pkey PRIMARY KEY (id);
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'users_email_key' AND conrelid =
              'public.users'::regclass) THEN
        ALTER TABLE public.users ADD CONSTRAINT users_email_key UNIQUE (email);
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'users_pkey' AND conrelid =
              'public.users'::regclass) THEN
        ALTER TABLE public.users ADD CONSTRAINT users_pkey PRIMARY KEY (id);
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'app_settings_updated_by_fkey' AND conrelid =
              'public.app_settings'::regclass) THEN
        ALTER TABLE public.app_settings ADD CONSTRAINT app_settings_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id) ON DELETE SET NULL;
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'entity_icons_updated_by_fkey' AND conrelid =
              'public.entity_icons'::regclass) THEN
        ALTER TABLE public.entity_icons ADD CONSTRAINT entity_icons_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id) ON DELETE SET NULL;
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'group_agents_group_id_fkey' AND conrelid =
              'public.group_agents'::regclass) THEN
        ALTER TABLE public.group_agents ADD CONSTRAINT group_agents_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(id) ON DELETE CASCADE;
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'group_members_added_by_fkey' AND conrelid =
              'public.group_members'::regclass) THEN
        ALTER TABLE public.group_members ADD CONSTRAINT group_members_added_by_fkey FOREIGN KEY (added_by) REFERENCES public.users(id) ON DELETE SET NULL;
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'group_members_group_id_fkey' AND conrelid =
              'public.group_members'::regclass) THEN
        ALTER TABLE public.group_members ADD CONSTRAINT group_members_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(id) ON DELETE CASCADE;
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'group_members_user_id_fkey' AND conrelid =
              'public.group_members'::regclass) THEN
        ALTER TABLE public.group_members ADD CONSTRAINT group_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'group_messages_author_id_fkey' AND conrelid =
              'public.group_messages'::regclass) THEN
        ALTER TABLE public.group_messages ADD CONSTRAINT group_messages_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.users(id) ON DELETE SET NULL;
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'group_messages_group_id_fkey' AND conrelid =
              'public.group_messages'::regclass) THEN
        ALTER TABLE public.group_messages ADD CONSTRAINT group_messages_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(id) ON DELETE CASCADE;
    END IF;
END
$guard$;

DO $guard$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'groups_created_by_fkey' AND conrelid =
              'public.groups'::regclass) THEN
        ALTER TABLE public.groups ADD CONSTRAINT groups_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;
    END IF;
END
$guard$;

COMMENT ON TABLE public.users IS 'Canonical application users. Owned by the harness. Humans only — external email correspondents belong in the mail server''s mailboxes table.';

-- ============================================================================
-- The agent's schema
--
-- One schema per agent: the isolation boundary. The agent's tables are
-- addressed through :"agent_name".<table>.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS :"agent_name";

CREATE TABLE IF NOT EXISTS :"agent_name".conversations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id bigint,
    agent_name text DEFAULT :'agent_name'::text NOT NULL,
    title text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    archived_at timestamp with time zone,
    is_group boolean DEFAULT false NOT NULL
);

CREATE TABLE IF NOT EXISTS :"agent_name".memories (
    id bigint NOT NULL,
    content text NOT NULL,
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, content)) STORED,
    embedding public.halfvec(1024),
    source_type text DEFAULT 'system'::text NOT NULL,
    source_ref text,
    importance smallint DEFAULT 5 NOT NULL,
    memory_hash text NOT NULL,
    status_commentary boolean DEFAULT false NOT NULL,
    permanent boolean DEFAULT false NOT NULL,
    pin_source text,
    pinned_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT 'operator'::text NOT NULL,
    CONSTRAINT memories_created_by_check CHECK ((created_by = ANY (ARRAY['operator'::text, 'agent'::text, 'import'::text]))),
    CONSTRAINT memories_importance_check CHECK (((importance >= 0) AND (importance <= 10))),
    CONSTRAINT memories_pin_source_check CHECK (((pin_source IS NULL) OR (pin_source = ANY (ARRAY['model'::text, 'operator'::text, 'bootstrap'::text])))),
    CONSTRAINT memories_source_type_check CHECK ((source_type = ANY (ARRAY['chat'::text, 'email'::text, 'code'::text, 'doc'::text, 'system'::text])))
);

CREATE TABLE IF NOT EXISTS :"agent_name".memory_audit (
    id bigint NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    op text NOT NULL,
    query_hash text NOT NULL,
    top_k integer DEFAULT 0 NOT NULL,
    result_count integer DEFAULT 0 NOT NULL,
    latency_ms integer DEFAULT 0 NOT NULL,
    op_over_run boolean DEFAULT false NOT NULL,
    CONSTRAINT memory_audit_op_check CHECK ((op = ANY (ARRAY['search'::text, 'pin'::text, 'unpin'::text, 'permanent_fetch'::text, 'write_turns'::text, 'list_recent'::text, 'write_memory'::text, 'recall_memory'::text, 'update_memory'::text, 'delete_memory'::text])))
);

CREATE TABLE IF NOT EXISTS :"agent_name".memory_chunks (
    id bigint NOT NULL,
    source_turn_id bigint,
    conversation_id uuid,
    role text NOT NULL,
    chunk_index integer DEFAULT 0 NOT NULL,
    content text NOT NULL,
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, content)) STORED,
    embedding public.halfvec(1024),
    source_type text DEFAULT 'chat'::text NOT NULL,
    source_ref text,
    importance smallint DEFAULT 5 NOT NULL,
    memory_hash text NOT NULL,
    status_commentary boolean DEFAULT false NOT NULL,
    permanent boolean DEFAULT false NOT NULL,
    pin_source text,
    pinned_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source_memory_id bigint,
    source_table text GENERATED ALWAYS AS (
CASE
    WHEN (source_turn_id IS NOT NULL) THEN 'turns'::text
    WHEN (source_memory_id IS NOT NULL) THEN 'memories'::text
    ELSE NULL::text
END) STORED,
    CONSTRAINT memory_chunks_importance_check CHECK (((importance >= 0) AND (importance <= 10))),
    CONSTRAINT memory_chunks_role_check CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text]))),
    CONSTRAINT memory_chunks_source_oneof_chk CHECK ((((source_turn_id IS NOT NULL) AND (source_memory_id IS NULL)) OR ((source_turn_id IS NULL) AND (source_memory_id IS NOT NULL)))),
    CONSTRAINT memory_chunks_source_type_check CHECK ((source_type = ANY (ARRAY['chat'::text, 'email'::text, 'code'::text, 'doc'::text, 'system'::text])))
);

CREATE TABLE IF NOT EXISTS :"agent_name".turns (
    id bigint NOT NULL,
    conversation_id uuid NOT NULL,
    role text NOT NULL,
    content text NOT NULL,
    token_count integer,
    model text,
    latency_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    attachments jsonb DEFAULT '[]'::jsonb NOT NULL,
    trajectory jsonb,
    CONSTRAINT turns_role_check CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text, 'system'::text])))
);

CREATE SEQUENCE IF NOT EXISTS :"agent_name".memories_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE SEQUENCE IF NOT EXISTS :"agent_name".memory_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE SEQUENCE IF NOT EXISTS :"agent_name".memory_chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE SEQUENCE IF NOT EXISTS :"agent_name".turns_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE :"agent_name".memories_id_seq OWNED BY :"agent_name".memories.id;

ALTER SEQUENCE :"agent_name".memory_audit_id_seq OWNED BY :"agent_name".memory_audit.id;

ALTER SEQUENCE :"agent_name".memory_chunks_id_seq OWNED BY :"agent_name".memory_chunks.id;

ALTER SEQUENCE :"agent_name".turns_id_seq OWNED BY :"agent_name".turns.id;

DO $default$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    EXECUTE format(
        'ALTER TABLE %I.%I ALTER COLUMN %I SET DEFAULT nextval(%L::regclass)',
        t, 'memories', 'id',
        t || '.memories_id_seq');
END
$default$;

DO $default$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    EXECUTE format(
        'ALTER TABLE %I.%I ALTER COLUMN %I SET DEFAULT nextval(%L::regclass)',
        t, 'memory_audit', 'id',
        t || '.memory_audit_id_seq');
END
$default$;

DO $default$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    EXECUTE format(
        'ALTER TABLE %I.%I ALTER COLUMN %I SET DEFAULT nextval(%L::regclass)',
        t, 'memory_chunks', 'id',
        t || '.memory_chunks_id_seq');
END
$default$;

DO $default$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    EXECUTE format(
        'ALTER TABLE %I.%I ALTER COLUMN %I SET DEFAULT nextval(%L::regclass)',
        t, 'turns', 'id',
        t || '.turns_id_seq');
END
$default$;

CREATE INDEX IF NOT EXISTS conversations_active_lookup_idx ON :"agent_name".conversations USING btree (user_id, updated_at DESC) WHERE ((archived_at IS NULL) AND (is_group = false));

CREATE INDEX IF NOT EXISTS memory_audit_op_idx ON :"agent_name".memory_audit USING btree (op, ts DESC);

CREATE INDEX IF NOT EXISTS memory_audit_ts_idx ON :"agent_name".memory_audit USING btree (ts DESC);

CREATE INDEX IF NOT EXISTS memory_chunks_content_trgm_idx ON :"agent_name".memory_chunks USING gin (content public.gin_trgm_ops);

CREATE INDEX IF NOT EXISTS memory_chunks_emb_hnsw_idx ON :"agent_name".memory_chunks USING hnsw (embedding public.halfvec_cosine_ops) WITH (m='16', ef_construction='64');

CREATE INDEX IF NOT EXISTS memory_chunks_permanent_idx ON :"agent_name".memory_chunks USING btree (permanent, pinned_at DESC) WHERE (permanent = true);

CREATE INDEX IF NOT EXISTS memory_chunks_tsv_idx ON :"agent_name".memory_chunks USING gin (content_tsv);

CREATE INDEX IF NOT EXISTS turns_conv_idx ON :"agent_name".turns USING btree (conversation_id, created_at);

SELECT format(
    'CREATE INDEX IF NOT EXISTS %I ON %I.memories %s',
    :'agent_name' || '_memories_created_at_idx',
    :'agent_name',
    'USING btree (created_at DESC)')
\gexec

SELECT format(
    'CREATE INDEX IF NOT EXISTS %I ON %I.memories %s',
    :'agent_name' || '_memories_emb_hnsw_idx',
    :'agent_name',
    'USING hnsw (embedding public.halfvec_cosine_ops) WITH (m=''16'', ef_construction=''64'')')
\gexec

SELECT format(
    'CREATE UNIQUE INDEX IF NOT EXISTS %I ON %I.memories %s',
    :'agent_name' || '_memories_memory_hash_key',
    :'agent_name',
    'USING btree (memory_hash)')
\gexec

SELECT format(
    'CREATE INDEX IF NOT EXISTS %I ON %I.memories %s',
    :'agent_name' || '_memories_permanent_idx',
    :'agent_name',
    'USING btree (permanent, pinned_at DESC) WHERE (permanent = true)')
\gexec

SELECT format(
    'CREATE INDEX IF NOT EXISTS %I ON %I.memories %s',
    :'agent_name' || '_memories_tsv_idx',
    :'agent_name',
    'USING gin (content_tsv)')
\gexec

SELECT format(
    'CREATE UNIQUE INDEX IF NOT EXISTS %I ON %I.memory_chunks %s',
    :'agent_name' || '_memory_chunks_hash_turn_key',
    :'agent_name',
    'USING btree (memory_hash, conversation_id) WHERE (source_turn_id IS NOT NULL)')
\gexec

SELECT format(
    'CREATE INDEX IF NOT EXISTS %I ON %I.memory_chunks %s',
    :'agent_name' || '_memory_chunks_source_memory_idx',
    :'agent_name',
    'USING btree (source_memory_id) WHERE (source_memory_id IS NOT NULL)')
\gexec

SELECT format(
    'CREATE INDEX IF NOT EXISTS %I ON %I.memory_chunks %s',
    :'agent_name' || '_memory_chunks_source_table_idx',
    :'agent_name',
    'USING btree (source_table)')
\gexec

DO $guard$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'conversations_pkey' AND conrelid =
              format('%I.%I', t, 'conversations')::regclass) THEN
        EXECUTE format('ALTER TABLE %I.conversations ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);', t);
    END IF;
END
$guard$;

DO $guard$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'memories_pkey' AND conrelid =
              format('%I.%I', t, 'memories')::regclass) THEN
        EXECUTE format('ALTER TABLE %I.memories ADD CONSTRAINT memories_pkey PRIMARY KEY (id);', t);
    END IF;
END
$guard$;

DO $guard$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'memory_audit_pkey' AND conrelid =
              format('%I.%I', t, 'memory_audit')::regclass) THEN
        EXECUTE format('ALTER TABLE %I.memory_audit ADD CONSTRAINT memory_audit_pkey PRIMARY KEY (id);', t);
    END IF;
END
$guard$;

DO $guard$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'memory_chunks_pkey' AND conrelid =
              format('%I.%I', t, 'memory_chunks')::regclass) THEN
        EXECUTE format('ALTER TABLE %I.memory_chunks ADD CONSTRAINT memory_chunks_pkey PRIMARY KEY (id);', t);
    END IF;
END
$guard$;

DO $guard$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'turns_pkey' AND conrelid =
              format('%I.%I', t, 'turns')::regclass) THEN
        EXECUTE format('ALTER TABLE %I.turns ADD CONSTRAINT turns_pkey PRIMARY KEY (id);', t);
    END IF;
END
$guard$;

DO $guard$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'memory_chunks_source_memory_id_fkey' AND conrelid =
              format('%I.%I', t, 'memory_chunks')::regclass) THEN
        EXECUTE format('ALTER TABLE %I.memory_chunks ADD CONSTRAINT memory_chunks_source_memory_id_fkey FOREIGN KEY (source_memory_id) REFERENCES %I.memories(id) ON DELETE CASCADE;', t, t);
    END IF;
END
$guard$;

DO $guard$
DECLARE
    t text := current_setting('harness.agent_name');
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'memory_chunks_source_turn_id_fkey' AND conrelid =
              format('%I.%I', t, 'memory_chunks')::regclass) THEN
        EXECUTE format('ALTER TABLE %I.memory_chunks ADD CONSTRAINT memory_chunks_source_turn_id_fkey FOREIGN KEY (source_turn_id) REFERENCES %I.turns(id) ON DELETE CASCADE;', t, t);
    END IF;
END
$guard$;

COMMIT;
