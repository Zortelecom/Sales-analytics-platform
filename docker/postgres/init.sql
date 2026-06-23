-- init.sql — Runs once on first container start (when postgres_data volume is empty)
-- Creates the two extra databases needed by Dagster and Superset.
-- The default database sap_meta is already created by the POSTGRES_DB env var.
--
-- Note: this script runs as POSTGRES_USER (set in docker-compose env).

-- Dagster run storage (run history, events, schedules, sensors)
CREATE DATABASE dagster;

-- Apache Superset metadata (dashboards, charts, users, RBAC)
CREATE DATABASE superset;

-- Grant full access on both to the platform user
GRANT ALL PRIVILEGES ON DATABASE dagster  TO current_user;
GRANT ALL PRIVILEGES ON DATABASE superset TO current_user;
