-- First initialization of a NEW production database only.
-- Read the app password inside psql; never interpolate it into a shell command.
\set app_password `cat /run/secrets/db_password`
CREATE ROLE fgtida LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE fgtida TO fgtida;
CREATE EXTENSION IF NOT EXISTS citext;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO fgtida;
\unset app_password
