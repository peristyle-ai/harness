-- The extensions the harness's schema needs.
--
-- Run this ONCE, as a SUPERUSER, before schema/init.sql:
--
--   sudo -u postgres psql -d <db> -f schema/extensions.sql
--
-- WHY IT IS SEPARATE
--   pgcrypto, pg_trgm and citext are TRUSTED extensions: a role with CREATE
--   on the database may install them. pgvector ("vector") is NOT trusted --
--   only a superuser may create it -- and the schema uses its halfvec type
--   for the embedding columns. So on a brand-new database the extensions
--   have to be installed before the operator applies the schema.
--
--   On a database where they already exist, schema/init.sql's own
--   CREATE EXTENSION IF NOT EXISTS lines are no-ops and need no privilege.
--
-- IDEMPOTENT: re-running it changes nothing.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "citext";
CREATE EXTENSION IF NOT EXISTS "vector";
