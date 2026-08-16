-- ============================================================================
-- PostgreSQL bootstrap for the sales analytics platform
--
--   psql -U postgres -f scripts/postgres_bootstrap.sql
--
-- Creates two databases and four roles. Run as a superuser, ONCE.
--
-- Change every password below before running. They are placeholders, and a
-- placeholder that survives to production is how a demo becomes an incident.
-- ============================================================================

-- ── Roles ───────────────────────────────────────────────────────────────────
--
-- Four roles rather than one, because they want genuinely different rights and
-- three of the four never need to write:
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
--
-- NOTE ON DUCKLAKE PERMISSIONS
-- DuckLake authorises at the CATALOG level, not per table. A role with write
-- access to the catalog can modify any table in the lake. Per-table control
-- would need a different mechanism entirely -- do not assume these grants give
-- you row- or table-level security. Row-level security for reporting stays
-- where it is, in reporting/utils/db.py.

CREATE ROLE lake_writer WITH LOGIN PASSWORD 'CHANGE_ME_writer';
CREATE ROLE sqlmesh_svc WITH LOGIN PASSWORD 'CHANGE_ME_sqlmesh';
CREATE ROLE bi_reader   WITH LOGIN PASSWORD 'CHANGE_ME_reader';
CREATE ROLE publisher   WITH LOGIN PASSWORD 'CHANGE_ME_publisher';

-- A group role that owns the catalog objects, so ownership does not depend on
-- which service happened to create a table first.
CREATE ROLE lake_owner WITH NOLOGIN;
GRANT lake_owner TO lake_writer, sqlmesh_svc;

-- ── Databases ───────────────────────────────────────────────────────────────
--
-- Two, not one. The DuckLake catalog and the SQLMesh state have different
-- lifecycles: state can be rebuilt by re-planning, the catalog cannot be
-- rebuilt without re-ingesting. Separate databases mean one can be dropped or
-- restored without touching the other, and a `DROP DATABASE sqlmesh_state` to
-- recover from a corrupt plan is a five-second decision rather than a careful one.

CREATE DATABASE ducklake_catalog OWNER lake_owner
  ENCODING 'UTF8' TEMPLATE template0;

-- SQLMesh creates its own tables, but not its own database.
CREATE DATABASE sqlmesh_state OWNER sqlmesh_svc
  ENCODING 'UTF8' TEMPLATE template0;

-- Nobody else needs to connect to these.
REVOKE CONNECT ON DATABASE ducklake_catalog FROM PUBLIC;
REVOKE CONNECT ON DATABASE sqlmesh_state    FROM PUBLIC;

GRANT CONNECT ON DATABASE ducklake_catalog
  TO lake_writer, sqlmesh_svc, bi_reader, publisher;
GRANT CONNECT ON DATABASE sqlmesh_state TO sqlmesh_svc;

\connect ducklake_catalog

-- ── Catalog grants ──────────────────────────────────────────────────────────
--
-- DuckLake creates its metadata tables (__ducklake_metadata_*) in `public` on
-- first attach. The writers need CREATE; the readers need only USAGE.

ALTER SCHEMA public OWNER TO lake_owner;
GRANT USAGE  ON SCHEMA public TO lake_writer, sqlmesh_svc, bi_reader, publisher;
GRANT CREATE ON SCHEMA public TO lake_writer, sqlmesh_svc;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
  TO lake_writer, sqlmesh_svc;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public
  TO lake_writer, sqlmesh_svc;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO bi_reader, publisher;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO bi_reader, publisher;

-- Default privileges matter more than the grants above: the metadata tables do
-- not exist yet, and DuckLake adds more over time (a snapshot table, a column
-- statistics table). Without these, a table created next month is invisible to
-- every reader and the failure looks like data loss.
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

-- SQLMesh owns this database entirely; no other role connects to it.
ALTER SCHEMA public OWNER TO sqlmesh_svc;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO sqlmesh_svc;

\connect postgres

-- ── Verify ──────────────────────────────────────────────────────────────────
SELECT rolname, rolcanlogin FROM pg_roles
WHERE rolname IN ('lake_writer','sqlmesh_svc','bi_reader','publisher','lake_owner')
ORDER BY rolname;

SELECT datname, pg_get_userbyid(datdba) AS owner FROM pg_database
WHERE datname IN ('ducklake_catalog','sqlmesh_state');
