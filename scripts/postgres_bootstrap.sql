-- ============================================================================
-- PostgreSQL bootstrap for the sales analytics platform
--
--   psql -U postgres -p 5433 -f scripts/postgres_bootstrap.sql
--
-- Pass -p explicitly, or set PGPORT: the \connect commands below reconnect
-- using the SAME parameters psql was invoked with, so a port given only in a
-- connection string would be inherited, but a port left to the 5432 default
-- would silently bootstrap a DIFFERENT cluster than the one you started.
--
-- SAFE TO RE-RUN. Every statement is either guarded or naturally idempotent,
-- so this is not a one-shot script: re-run it after the first successful
-- DuckLake attach to catch up the GRANT ... ON ALL TABLES statements, and
-- again any time a role's password is rotated.
--
-- WHY \gexec RATHER THAN DO $$ ... $$
-- CREATE DATABASE cannot run inside a transaction block, and a DO block is a
-- transaction block -- so databases cannot be created that way. \gexec runs
-- the *text* returned by a SELECT as its own top-level statement, which gives
-- the same "only if missing" guard without the transaction.
--
-- psql also refuses to interpolate :variables inside dollar-quoted strings,
-- so passwords could not reach a DO block anyway. format(%L) in a \gexec query
-- quotes them correctly instead.
--
-- PASSWORDS COME FROM THE ENVIRONMENT, WITH PLACEHOLDER FALLBACKS
--
--   PowerShell:  $env:PG_CATALOG_PASSWORD = 'new_secret'
--                psql -U postgres -p 5433 -f scripts/postgres_bootstrap.sql
--
-- A placeholder that survives to production is how a demo becomes an incident.
--
-- ─────────────────────────────────────────────────────────────────────────
-- (2026-08) TWO CHANGES
--
-- 1. THE SET ROLE IS NOW SCOPED PER DATABASE.
--
--    It used to be:
--        ALTER ROLE sqlmesh_svc SET "role" = 'lake_owner';
--
--    With no IN DATABASE clause that applies to EVERY database the role
--    connects to -- including sqlmesh_state, where lake_owner owns nothing
--    and is granted nothing. So SQLMesh authenticated fine (CONNECT is
--    checked as sqlmesh_svc, before the role switch), then failed on the
--    first CREATE TABLE of its state migration. SQLMesh's rollback handler
--    then issued another query inside the now-aborted transaction, so the
--    error surfaced as
--        psycopg2.errors.InFailedSqlTransaction
--    which names neither the role nor the permission, and buries the real
--    "permission denied for schema public" entirely.
--
--    The SET ROLE is still correct and still wanted -- it is what keeps
--    ownership of the DuckLake metadata tables on lake_owner rather than on
--    whichever writer attached first. It just has no business outside the
--    catalog database.
--
-- 2. DAGSTER STORAGE IS NOW PROVISIONED HERE.
--
--    Dagster's default SQLite instance storage collapsed under the
--    multiprocess executor:
--        sqlite3.OperationalError: disk I/O error
--    thrown from the SchedulerDaemon, which then never recovered -- the run
--    completed successfully while the scheduler sat dead, so the daily
--    schedule would simply not have fired. Ten step subprocesses against one
--    SQLite file is not a workload SQLite is meant for.
--
--    dagster_svc is deliberately NOT a member of lake_owner and gets no
--    SET ROLE. It has no business in the lake, and giving it none means the
--    failure above cannot repeat in a new form.
-- ============================================================================

\set ON_ERROR_STOP on

\getenv lake_writer_pw PG_CATALOG_PASSWORD
\getenv sqlmesh_pw     PG_CATALOG_PASSWORD_SQLMESH
\getenv bi_reader_pw   PG_CATALOG_PASSWORD_READER
\getenv publisher_pw   PG_CATALOG_PASSWORD_PUBLISHER
\getenv dagster_pw     PG_CATALOG_PASSWORD_DAGSTER

\if :{?lake_writer_pw} \else \set lake_writer_pw 'this_is_the_lake_writer_2026' \endif
\if :{?sqlmesh_pw}     \else \set sqlmesh_pw     'this_is_the_sqlmesh_svc_2026' \endif
\if :{?bi_reader_pw}   \else \set bi_reader_pw   'this_is_the_bi_reader_2026'   \endif
\if :{?publisher_pw}   \else \set publisher_pw   'this_is_the_publisher_2026'   \endif
\if :{?dagster_pw}     \else \set dagster_pw     'this_is_the_dagster_svc_2026' \endif


-- ── Roles ───────────────────────────────────────────────────────────────────
--
-- Five login roles rather than one, because they want genuinely different
-- rights and two of the five never need to write:
--
--   lake_writer   ingestion. Creates and updates DuckLake metadata tables when
--                 landing rows are appended.
--   sqlmesh_svc   transformation. Creates and drops tables and views in the
--                 lake -- which in DuckLake means writing catalog metadata --
--                 and owns the SQLMesh state database outright.
--   bi_reader     Streamlit, Superset, Metabase. Read only.
--   publisher     serving/publish.py. Read only. Separate from bi_reader so
--                 that revoking a BI tool's access does not stop Power BI
--                 being refreshed, and so the connection counts are legible.
--   dagster_svc   Dagster run, event-log and schedule storage. Touches its own
--                 database and nothing else -- NOT a member of lake_owner.
--
-- Plus one group role, lake_owner (NOLOGIN), which owns the catalog database
-- and its public schema so that ownership does not depend on which service
-- happened to connect first.
--
-- NOTE ON DUCKLAKE PERMISSIONS
-- DuckLake authorises at the CATALOG level, not per table. A role with write
-- access to the catalog can modify any table in the lake. Per-table control
-- would need a different mechanism entirely -- do not assume these grants give
-- you row- or table-level security. Row-level security for reporting stays
-- where it is, in reporting/utils/db.py.

-- CREATE when missing, ALTER when present -- one statement either way, so a
-- re-run rotates the password rather than failing on "role already exists".
SELECT format('%s ROLE %I WITH LOGIN PASSWORD %L',
              CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.name)
                   THEN 'ALTER' ELSE 'CREATE' END, r.name, r.pw)
FROM (VALUES ('lake_writer', :'lake_writer_pw'),
             ('sqlmesh_svc', :'sqlmesh_pw'),
             ('bi_reader',   :'bi_reader_pw'),
             ('publisher',   :'publisher_pw'),
             ('dagster_svc', :'dagster_pw')) AS r(name, pw)
\gexec

SELECT 'CREATE ROLE lake_owner WITH NOLOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lake_owner')
\gexec

-- Membership. dagster_svc is absent by design.
GRANT lake_owner TO lake_writer, sqlmesh_svc;


-- ── Databases ───────────────────────────────────────────────────────────────
--
-- Three, not one. They have different lifecycles: SQLMesh state can be rebuilt
-- by re-planning, Dagster storage can be discarded entirely (losing history,
-- not data), and the catalog cannot be rebuilt without re-ingesting. Separate
-- databases mean `DROP DATABASE sqlmesh_state` to recover from a corrupt plan
-- is a five-second decision rather than a careful one.

SELECT 'CREATE DATABASE ducklake_catalog OWNER lake_owner '
       'ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'ducklake_catalog')
\gexec

-- SQLMesh creates its own tables and schema, but not its own database.
SELECT 'CREATE DATABASE sqlmesh_state OWNER sqlmesh_svc '
       'ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'sqlmesh_state')
\gexec

-- Dagster likewise creates its own tables on first start.
SELECT 'CREATE DATABASE dagster_storage OWNER dagster_svc '
       'ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'dagster_storage')
\gexec

-- Corrects the owner if a database was created by hand with the wrong one.
ALTER DATABASE ducklake_catalog OWNER TO lake_owner;
ALTER DATABASE sqlmesh_state    OWNER TO sqlmesh_svc;
ALTER DATABASE dagster_storage  OWNER TO dagster_svc;

-- Nobody else needs to connect to these.
REVOKE CONNECT ON DATABASE ducklake_catalog FROM PUBLIC;
REVOKE CONNECT ON DATABASE sqlmesh_state    FROM PUBLIC;
REVOKE CONNECT ON DATABASE dagster_storage  FROM PUBLIC;

GRANT CONNECT ON DATABASE ducklake_catalog
  TO lake_writer, sqlmesh_svc, bi_reader, publisher;
GRANT CONNECT ON DATABASE sqlmesh_state   TO sqlmesh_svc;
GRANT CONNECT ON DATABASE dagster_storage TO dagster_svc;


-- ── The SET ROLE, scoped ────────────────────────────────────────────────────
--
-- RESET first so that re-running this script over a cluster bootstrapped by
-- the previous version removes the global setting rather than leaving it
-- alongside the scoped one. RESET on a role that never had it set is a no-op.
--
-- WHY THIS EXISTS AT ALL
-- DuckLake creates its __ducklake_metadata_* tables on first attach, and in
-- this platform that is ingestion (lake_writer). Without the SET ROLE,
-- lake_writer owns them and sqlmesh_svc -- which needs to ALTER them when a
-- DuckLake version upgrade migrates the metadata schema -- cannot, because
-- ALTER requires ownership and no GRANT confers it. The failure would arrive
-- months later, on an unrelated `pip install -U`, as a permission error in
-- the middle of a plan.
--
-- Note the consequence: inside ducklake_catalog, current_user is lake_owner
-- while session_user stays lake_writer. pg_stat_activity still shows the real
-- identity in usename, so the per-role connection audit is unaffected.
--
-- shared/verify.py checks for the unscoped form by comparing current_user
-- against session_user on the SQLMesh state connection.
--
-- To revert entirely:  ALTER ROLE lake_writer RESET "role";
ALTER ROLE lake_writer RESET "role";
ALTER ROLE sqlmesh_svc RESET "role";

ALTER ROLE lake_writer IN DATABASE ducklake_catalog SET "role" = 'lake_owner';
ALTER ROLE sqlmesh_svc IN DATABASE ducklake_catalog SET "role" = 'lake_owner';


\connect ducklake_catalog

-- ── Catalog grants ──────────────────────────────────────────────────────────
--
-- DuckLake creates its metadata tables (__ducklake_metadata_*) in `public` on
-- first attach. The writers need CREATE; the readers need only USAGE.
--
-- PostgreSQL 15 removed the implicit CREATE grant on public to PUBLIC, so
-- these are not belt-and-braces on a modern server -- without them a fresh
-- non-superuser role cannot create anything at all.

ALTER SCHEMA public OWNER TO lake_owner;
REVOKE CREATE  ON SCHEMA public FROM PUBLIC;
GRANT  USAGE   ON SCHEMA public TO lake_writer, sqlmesh_svc, bi_reader, publisher;
GRANT  CREATE  ON SCHEMA public TO lake_writer, sqlmesh_svc;

-- These cover tables that exist RIGHT NOW, which on a first run is none of
-- them -- "ALL TABLES" is expanded at grant time, not stored as a rule. That
-- is why information_schema.table_privileges is empty before the first attach,
-- and why re-running this script afterwards is worth doing.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
  TO lake_writer, sqlmesh_svc;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public
  TO lake_writer, sqlmesh_svc;

GRANT SELECT ON ALL TABLES    IN SCHEMA public TO bi_reader, publisher;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO bi_reader, publisher;

-- Default privileges matter more than the grants above: the metadata tables do
-- not exist yet, and DuckLake adds more over time (a snapshot table, a column
-- statistics table). Without these, a table created next month is invisible to
-- every reader and the failure looks like data loss.
--
-- Default privileges are keyed to the role that CREATES the object, so every
-- role that could create one needs its own set. Inspect them with \ddp --
-- they do NOT appear in information_schema.table_privileges.
--
-- With the scoped SET ROLE above, objects are created BY lake_owner, so that
-- block is the one that does the work in practice. The lake_writer and
-- sqlmesh_svc blocks remain for the case where the SET ROLE is reverted.
ALTER DEFAULT PRIVILEGES FOR ROLE lake_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lake_writer, sqlmesh_svc;
ALTER DEFAULT PRIVILEGES FOR ROLE lake_owner IN SCHEMA public
  GRANT SELECT ON TABLES TO bi_reader, publisher;
ALTER DEFAULT PRIVILEGES FOR ROLE lake_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO lake_writer, sqlmesh_svc, bi_reader, publisher;

ALTER DEFAULT PRIVILEGES FOR ROLE lake_writer IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sqlmesh_svc;
ALTER DEFAULT PRIVILEGES FOR ROLE lake_writer IN SCHEMA public
  GRANT SELECT ON TABLES TO bi_reader, publisher;
ALTER DEFAULT PRIVILEGES FOR ROLE lake_writer IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO sqlmesh_svc, bi_reader, publisher;

ALTER DEFAULT PRIVILEGES FOR ROLE sqlmesh_svc IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lake_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE sqlmesh_svc IN SCHEMA public
  GRANT SELECT ON TABLES TO bi_reader, publisher;
ALTER DEFAULT PRIVILEGES FOR ROLE sqlmesh_svc IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO lake_writer, bi_reader, publisher;


\connect sqlmesh_state

-- SQLMesh owns this database entirely; no other role connects to it. It
-- creates its own schema (default `sqlmesh`), so CREATE on the DATABASE is
-- what matters here -- which sqlmesh_svc has as owner. The public grant is
-- still required on PostgreSQL 15+ for the case where SQLMesh falls back to
-- creating state objects in public.
ALTER SCHEMA public OWNER TO sqlmesh_svc;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT  ALL ON SCHEMA public TO sqlmesh_svc;


\connect dagster_storage

-- Dagster creates run storage, event log storage and schedule storage tables
-- in public on first start. Same PostgreSQL 15+ requirement.
ALTER SCHEMA public OWNER TO dagster_svc;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT  ALL ON SCHEMA public TO dagster_svc;


\connect postgres

-- ── Verify ──────────────────────────────────────────────────────────────────
\echo ''
\echo '=== Roles (dagster_svc must NOT be a member of lake_owner) ==='
SELECT r.rolname, r.rolcanlogin, r.rolsuper,
       pg_has_role(r.rolname, 'lake_owner', 'MEMBER') AS in_lake_owner
FROM pg_roles r
WHERE r.rolname IN ('lake_writer','sqlmesh_svc','bi_reader','publisher',
                    'dagster_svc','lake_owner')
ORDER BY r.rolcanlogin, r.rolname;

\echo ''
\echo '=== Role settings — every row MUST name a database ==='
\echo '(a NULL database is the global SET ROLE that breaks SQLMesh state)'
SELECT r.rolname,
       COALESCE(d.datname, '*** GLOBAL — THIS IS THE BUG ***') AS scope,
       s.setconfig
FROM pg_db_role_setting s
JOIN pg_roles r ON r.oid = s.setrole
LEFT JOIN pg_database d ON d.oid = s.setdatabase
ORDER BY r.rolname;

\echo ''
\echo '=== Databases ==='
SELECT datname, pg_get_userbyid(datdba) AS owner,
       pg_encoding_to_char(encoding) AS encoding,
       datcollate,
       has_database_privilege('lake_writer', datname, 'CONNECT') AS lake_writer,
       has_database_privilege('bi_reader',   datname, 'CONNECT') AS bi_reader,
       has_database_privilege('dagster_svc', datname, 'CONNECT') AS dagster_svc
FROM pg_database
WHERE datname IN ('ducklake_catalog','sqlmesh_state','dagster_storage')
ORDER BY datname;

\connect ducklake_catalog

\echo ''
\echo '=== Schema privileges on public (ducklake_catalog) ==='
SELECT r.rolname,
       has_schema_privilege(r.rolname, 'public', 'USAGE')  AS usage,
       has_schema_privilege(r.rolname, 'public', 'CREATE') AS create
FROM pg_roles r
WHERE r.rolname IN ('lake_writer','sqlmesh_svc','bi_reader','publisher')
ORDER BY r.rolname;

\echo ''
\echo '=== Default privileges (these govern tables DuckLake has not created yet) ==='
SELECT pg_get_userbyid(d.defaclrole) AS creating_role,
       n.nspname AS schema,
       CASE d.defaclobjtype WHEN 'r' THEN 'tables' WHEN 'S' THEN 'sequences'
                            ELSE d.defaclobjtype::text END AS applies_to,
       d.defaclacl::text AS acl
FROM pg_default_acl d
JOIN pg_namespace n ON n.oid = d.defaclnamespace
ORDER BY creating_role, applies_to;

\echo ''
\echo '=== Tables currently in public ==='
\echo '(0 before the first successful DuckLake attach -- expected. Re-run this'
\echo ' script afterwards so the GRANT ... ON ALL TABLES statements catch up.)'
SELECT count(*) AS tables_in_public
FROM information_schema.tables WHERE table_schema = 'public';

\echo ''
\echo 'Next: python -m shared.verify'
