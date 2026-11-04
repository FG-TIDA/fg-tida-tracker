# Render deployment for FG-TIDA

This profile runs one paid Docker Web Service, a 64 MB loopback-only Memcached
process shared by its Gunicorn workers, a persistent `/data` disk, and managed
PostgreSQL. No Redis service or Celery worker is required. Celery publishing
raises an explicit error in this profile; reviewed FG-TIDA mail uses the durable
PostgreSQL outbox. Do not run upstream Celery workers/beat with this profile.

## Service settings

- Repository: `FG-TIDA/fg-tida-tracker`; reviewed branch: `main`.
- Dockerfile: `deploy/Dockerfile`; build context: `.`; root directory: empty.
- Same region as PostgreSQL. Start with one paid instance and monitor memory.
- Disk: `/data`, initially 5 GB; the unprivileged tracker UID 10001 must be able
  to write to it. Do not run the website as root to work around permissions.
- Health check: `/health/`. No registry credential or Docker command override.
- Pre-deploy command: empty for the first import. Auto-deploy: Off initially.
- Use Render's actual service Shell/SSH for disk restoration, not an ephemeral
  shell, pre-deploy command or one-off job, which cannot access the web disk.

Set these environment variables in the dashboard (no credentials in Git):

```text
FGTIDA_ENV=production
FGTIDA_HOSTING=render
FGTIDA_RENDER_MODE=bootstrap
FGTIDA_ORIGIN=https://tida-datatracker.org
FGTIDA_APPROVED_PRIVILEGED_USERS=<approved administrator username>
FGTIDA_DB_HOST=<internal database hostname>
FGTIDA_DB_PORT=5432
FGTIDA_DB_NAME=<actual database name>
FGTIDA_DB_USER=fgtida_app
FGTIDA_DB_PASSWORD_FILE=/etc/secrets/db_password.key
FGTIDA_DB_SSLMODE=require
FGTIDA_SECRET_KEY_FILE=/etc/secrets/django_key.key
FGTIDA_DATA_DIR=/data
FGTIDA_MAIL_ENABLED=false
# Opt in only after confirming all existing web subdomains support HTTPS.
FGTIDA_HSTS_INCLUDE_SUBDOMAINS=true
WEB_CONCURRENCY=2
PORT=8000
```

Render supplies `RENDER_EXTERNAL_HOSTNAME`. The profile allows only the canonical
domain and that exact validated `*.onrender.com` service hostname; it does not
enable wildcard hosts or trust the temporary hostname for account POSTs.
GET/HEAD requests to the temporary hostname redirect to the canonical origin.
Health requests validate Host and query PostgreSQL before returning 200 in
normal modes. They do not redirect to login or HTTPS.

Create secret files `db_password.key` (application-role password) and
`django_key.key` (new random production signing key, at least 64 random
characters). Their contents are only the secret value. The image grants the
runtime user Render's secret-reading group 1000. `.dockerignore` excludes all
`*.key` files from image copies. Never paste a database URL into a screenshot,
commit, issue or chat. No `DATABASE_URL`, external Memcached variables, broker
URL, development settings or SMTP credentials are required for bootstrap.

## Modes and initial import

`bootstrap` serves only a fixed maintenance response: HTTP 503 everywhere except
HTTP 200 for GET/HEAD `/health/`. It loads no Django settings, records or files.
This health response means the maintenance process is up, not that the platform
is ready. Bootstrap is intentional and must not be announced as the live site.

`staged` requires all publication checks, including administrator MFA, to pass
except SMTP. Email stays in the database; real mail-dependent account recovery
is unavailable. Use this mode for final preparation while ITU SMTP is pending.

`production` is the default and requires the complete publication gate, including
SMTP configuration. Neither normal mode bypasses MFA, HTTPS, data-scope, account
allowlist or file checks. They start cache, check publication, collect static
files into `/data/static`, then run Gunicorn. An unexpected cache/server exit
stops the whole service. SIGTERM stops both children. Static files are served by
WhiteNoise; uploaded media is never published as an unrestricted directory.

1. Deploy in `bootstrap` mode. Check `/` is 503 and `/health/` is 200.
2. Before the final local export, enroll the administrator's authenticator in
   the local platform with the development MFA pause disabled. Keep the actual
   authenticator and regenerate recovery codes after switching signing keys.
   Do not fabricate an enrolled device merely to pass publication checks.
3. Freeze source edits and export only FG-TIDA records and corresponding files:

   ```powershell
   docker exec fg-tida-datatracker-app-1 python ietf/manage.py export_fgtida_release --administrator YOUR_ADMIN_USERNAME --output /workspace/dev/security-private/cutover/fgtida.json --skip-checks
   docker exec fg-tida-datatracker-app-1 python ietf/manage.py export_fgtida_files --fixture /workspace/dev/security-private/cutover/fgtida.json --output /workspace/dev/security-private/cutover/fgtida-files.zip --skip-checks
   ```

   Use a fresh output directory for a new export; existing exports are not
   overwritten. The fixture contains private accounts/MFA and must stay private.
4. Add your public SSH key to Render; keep the private key locally. Open
   Connect > SSH on the Web Service and copy its `user@host` destination.
   Verify the SSH host fingerprint against Render's published fingerprint.
   With a local Python installation and OpenSSH, run from the repository:

   ```powershell
   python deploy/upload_render_import.py --destination YOUR_RENDER_USER@YOUR_RENDER_SSH_HOST --identity-file "$env:USERPROFILE\.ssh\fgtida_render_ed25519" --fixture dev/security-private/cutover/fgtida.json --files dev/security-private/cutover/fgtida-files.zip
   ```

   This streams binary files through SSH without PowerShell byte pipelines,
   stores them in a private `/tmp/fgtida-import` directory and verifies SHA-256.
   It preserves strict verification of the already-trusted server host key,
   disables additional host-key updates for Render's SSH compatibility, and
   selects the dedicated key when `--identity-file` is supplied. SSH prompts
   locally for its passphrase; never include the passphrase in a command.
   It requires standard remote-command SSH support; do not substitute a public
   download link if your account's SSH interface rejects remote commands.
5. Open the **actual service's** Shell/SSH. On a new empty production database,
   using the restricted application role, run:

   ```sh
   python ietf/manage.py migrate --noinput --skip-checks
   python ietf/manage.py loaddata /tmp/fgtida-import/fgtida.json --skip-checks
   python ietf/manage.py configure_fgtida --skip-checks
   python ietf/manage.py restore_fgtida_files --archive /tmp/fgtida-import/fgtida-files.zip --skip-checks
   python ietf/manage.py check_fgtida_publication --allow-deferred-smtp --skip-checks
   ```

   Do not load the fixture into an existing live database. The restore command
   verifies every file before writing, rejects unsafe paths and mismatches,
   skips identical existing files and refuses to replace different files.
6. Remove only the two uploaded import files when finished:

   ```sh
   rm /tmp/fgtida-import/fgtida.json /tmp/fgtida-import/fgtida-files.zip
   ```

   Keep independent encrypted source exports for recovery.
   Bootstrap deliberately does not start Memcached. The explicit publication
   command above still checks MFA, data scope, role privileges, migrations and
   files; `--skip-checks` postpones Django's general registry checks until the
   normal service has started its cache. Do not re-import the fixture to fix a
   cache-connection error.
7. Configure and verify the canonical custom domain/DNS/HTTPS, then set
   `FGTIDA_RENDER_MODE=staged` and redeploy. Check actual pages, login, MFA,
   contributions, agenda times, file download and persistence after redeploy.
   Regenerate administrator recovery codes under the production signing key.
   In the running staged service's Shell, complete the general deployment check:

   ```sh
   python ietf/manage.py check --deploy
   ```

   Startup creates empty upload directories on the mounted disk, including
   `/data/media/photo`; a file export cannot preserve a directory with no files.
   Existing uploads are preserved, and symlinks outside the disk are rejected.

   FG-TIDA does not expose the inherited API router or personal API-key pages.
   Their menu entries are removed, and API routes return 404 for signed-in and
   anonymous users. Administrative browser workflows remain available.

   HSTS uses a one-year duration. With the explicit subdomain opt-in above, all
   current and future web subdomains must support HTTPS. Browser preloading
   remains disabled: https://hstspreload.org/ currently recommends ordinary
   HSTS but does not recommend preloading. Only optional check `security.W021`
   is recorded as an intentional exception in the Render profile; W005 and
   other security checks are retained. This does not submit the domain to a
   browser preload list or disable TLS verification.
8. After ITU authorizes SMTP, configure and test it, set
   `FGTIDA_MAIL_ENABLED=true`, and switch to `FGTIDA_RENDER_MODE=production`.
   Schedule `python ietf/manage.py deliver_fgtida_mail --skip-checks` each minute
   and `python ietf/manage.py prune_authentication_attempts --skip-checks` daily
   in separate Docker Cron Jobs with explicit command overrides. Jobs need the
   database/secret configuration and may use `/tmp/fgtida-job-data` for
   `FGTIDA_DATA_DIR`; they do not mount the web disk or start local cache.

## Redeploying the API and email-identity update

Take a managed PostgreSQL backup and preserve the artifact backup before the
update. Set `FGTIDA_HSTS_INCLUDE_SUBDOMAINS=true` only after confirming your
current web subdomains support HTTPS; retain staged mode and disabled mail while
ITU SMTP is pending. Keep the existing database, `/data` disk, Django signing key
and administrator account. Do not reload the release fixture on redeployment.

Set this Render **Pre-Deploy Command** before deploying the updated commit:

```sh
python ietf/manage.py migrate --noinput --skip-checks
```

The email migration repairs fresh Django 5 installations whose historical email
columns were created as varchar instead of citext. Django updates the email
primary key, related foreign keys and history column atomically. Account
passwords, MFA devices and email values are preserved. It refuses case-colliding
email identities instead of deleting or merging accounts. The migration is
deliberately irreversible to prevent downgrading identity uniqueness; previous
application code already expects citext and remains compatible with the repair.
The initial database-owner setup should already have installed citext. If that
extension is missing and the restricted role cannot install it, have the owner
install it; do not grant the website superuser privileges.

After a successful deploy, open a new service Shell and run `check --deploy` and
`check_fgtida_publication --allow-deferred-smtp --skip-checks`. Verify login/MFA,
admin user management, document uploads/downloads and the agenda. API-key pages
and `/api/` must return 404. The optional preload-policy warning is documented
above; do not disable other security checks to obtain a clean result.

## Publication and ongoing operation

Run a fresh image/dependency/secret scan before public launch. The previous
release's recorded OS advisories are not cleared merely by adding this adapter.
Validate backup restoration of both PostgreSQL and `/data`. PostgreSQL 18
requires its own migration/restore validation; earlier local validation used 17.
Disk and database storage are independent and must both be monitored.

After initial import, a pre-deploy migration command may be configured only
after verifying migrations are backward compatible with the still-running old
version. Never put fixture imports or disk restoration in pre-deploy commands.
The attached disk means one instance and downtime during deployment. To scale
horizontally later, first implement/test shared upload storage and a shared
cache, then load-test session, upload and task behavior across instances.

References: [Docker secrets](https://render.com/docs/docker-secrets),
[health checks](https://render.com/docs/health-checks),
[persistent disks](https://render.com/docs/disks),
[SSH](https://render.com/docs/ssh),
[WhiteNoise](https://whitenoise.readthedocs.io/en/stable/django.html).
