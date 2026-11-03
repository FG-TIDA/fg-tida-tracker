# FG-TIDA production release

This is a separate production deployment. Do not expose the development Compose stack or copy its full IETF sample database into production.

## Required hosting configuration

1. Confirm the public HTTPS domain. SMTP is intentionally deferred: preparation runs with `FGTIDA_MAIL_ENABLED=false` and requires no SMTP credentials. Before public launch, ITU must authorize the sender and delivery must be tested. See [the handover checklist](LAUNCH-CHECKLIST.md).
2. Copy `production.env.example` to the ignored `production.env` and replace all example values. Replace `admin-example` with the actual authorized administrator username in this private configuration. Add later trusted administrators to this list before the release check.
3. Run `python deploy/generate_secrets.py` once to generate private `secrets/django_key`, `secrets/db_password` and `secrets/db_owner_password` without printing them. It never overwrites existing values. Restrict these files to the deployment administrator. Later add the ITU SMTP secret and use the `compose.smtp.yml` overlay when enabling mail.
4. Place the proxy's loopback port 8080 behind the hosting TLS terminator. Preserve the requested Host; overwrite X-Real-IP with the actual client IP and discard untrusted forwarding headers. Set `FGTIDA_TRUSTED_PROXY_CIDR` to that proxy's exact address as seen by nginx, so account rate limits apply per client. Do not expose the app, database, queue, cache or development ports. The provided proxy sets the HTTPS forwarding header because its listener is intended exclusively for that trusted TLS terminator.
5. Use supported host/container OS releases, configure backups and monitoring, and pin validated base/service images by digest for the actual release. Rebuild and rescan when dependencies or images change.

## Build and initialize

Run from the repository root:

```sh
docker compose -f deploy/compose.yml build --pull --no-cache
docker compose -f deploy/compose.yml up -d db cache queue
docker compose -f deploy/compose.yml run --rm app python ietf/manage.py migrate --noinput
```

The app image runs as an unprivileged user. Its application filesystem is read-only; `/data` holds persistent uploads and collected static files. Production uses filesystem artifact storage, so the `/data` volume is part of every backup.

## Transfer only approved FG-TIDA records

The management command below exports the FG-TIDA record graph, document history, meetings, memberships and dependencies with only the named account. It refuses an out-of-scope document/group/meeting reference. It never deletes source data.

```sh
python ietf/manage.py export_fgtida_release --administrator admin-example --output /private/release.json
python ietf/manage.py export_fgtida_files --fixture /private/release.json --output /private/release-files.zip
```

The fixture contains password hashes, personal profile data and confirmed authenticator secrets. Encrypt it in transit and at rest. Enroll administrator MFA before export; regenerate recovery codes after transfer because the production Django key changes. Treat it as confidential, transfer it privately, and do not place it under static/media paths or commit it. Import it into the fresh production database using `loaddata`, then run `configure_fgtida`. This sets the active Site to `FGTIDA_ORIGIN`, fixes vocabulary labels and creates the limited editor role.

Uploaded files are separate from the database fixture. The file export includes current and historical revisions, extracted text, meeting floor plans and referenced member photos, with a SHA-256 manifest. Extract this trusted archive into the production `/data` volume and verify the checksums. Validate every reference before release. Do not copy unrelated IETF artifacts. Keep the source database and assets until the new deployment has passed restore and workflow verification.

```sh
docker compose -f deploy/compose.yml run --rm app python ietf/manage.py configure_fgtida
docker compose -f deploy/compose.yml run --rm app python ietf/manage.py collectstatic --noinput
docker compose -f deploy/compose.yml run --rm app python ietf/manage.py check --deploy
docker compose -f deploy/compose.yml run --rm app python ietf/manage.py check_fgtida_publication
```

`check_fgtida_publication` rejects development mode, insecure cookies, a wrong Site domain, pending migrations, unapproved privileged users, non-FG documents/meetings and missing current uploads. It is an automated release gate, not a substitute for verifying delivery and recovery.

Start `app`, `worker` and `proxy` only after these checks pass. Verify registration and password-reset delivery using actual mailboxes, administrator login, document upload/download/history, membership edits, agendas and time zones, important dates, cancellations, and anonymous access restrictions.

## Authentication and launch security

Contribution routing: in **Admin → Groups**, open WG1, WG2, WG3 or Incubation and add active addresses under **Contribution notification recipients**. Use WG member addresses for working groups and FG management addresses for Incubation. Recipients receive separate messages containing the original submitted attachment. Addresses are not published. A submission without configured recipients remains stored with `notifications_queued=False`; saving the group's recipient configuration queues up to 100 pending submissions and the scheduled outbox job handles the remainder. `deliver_fgtida_mail` queues notifications and retries delivery after SMTP is enabled. Retries retain the original attachment; removed or deactivated recipients' pending messages are cancelled. Review the addresses before enabling SMTP.

Local development pauses the administrator MFA gate only while `DEBUG` is enabled and `SERVER_MODE` is `development`. Existing authenticators are retained. Production continues to require administrator enrollment and verification. To exercise MFA locally, launch the development server with environment variable `FGTIDA_DEV_ADMIN_MFA_PAUSED=false`.

Stable homepage text and public/contact links live in `ietf/fgtida/content.py` and require no database lookup. Documents and their revisions, meetings, agenda sessions, materials, members, milestones and important dates remain database-backed and editable through their administration pages. Contribution notifications go to the group's private recipient list; authorized SMTP credentials remain deployment settings.

Administrators must enroll an authenticator under **User → Two-factor authentication**. Regular members can enable it voluntarily. Save the ten one-use recovery codes outside the server. Security questions are legacy recovery checks, not a second factor. Superusers can add, edit and retire choices under **Security questions** in admin; keep at least three active. Existing enrollments retain their original prompt text.

The publication check also requires enabled mail delivery, enrolled administrators and a database role without superuser, CREATEROLE or CREATEDB privileges. Test actual reset-email delivery separately. Normal sign-in goes to the home page; MFA enrollment or verification is required first when applicable.

Use new, distinct database owner/application passwords and a new Django signing key for production. Changing a Compose secret file does not rotate a password inside an existing PostgreSQL volume. Perform rotation with the database administrator, update the mounted secret, restart affected clients and verify connectivity. Never put credentials in Git or command-line arguments. Any real credential previously published remains compromised even after its source line is removed.

Authenticator seeds are stored in PostgreSQL by django-otp and are sensitive. Encrypt database disks, backups and release fixtures, and restrict database access. After a signing-key change, regenerate recovery codes. Lost-authenticator recovery requires verified identity and a controlled operator procedure; password reset must not remove MFA.

Configure hosting-console MFA, SSH keys and restricted administrative network access once the host is chosen. Do not publicly expose PostgreSQL or pgAdmin. Schedule `python ietf/manage.py prune_authentication_attempts` daily to remove expired rate-limit rows. Confirm backups by restoring them into an isolated environment.

## Backup verification

Back up PostgreSQL and `/data` together under a maintenance window or consistent snapshot. Encrypt backups and restrict access. Restore into an isolated database/volume and exercise document downloads and login before treating a backup as usable. Retain previous image digests and schema-compatible backups for rollback.

`dev/verify_fgtida_restore.py` is a local verification helper for the private release fixture; it uses a dedicated `fgtida_release_verification` database and does not write to the source database.

Audit Python with `pip-audit` against the actual installed inventory, JavaScript with `npm audit`, and container OS packages with an image scanner. A clean advisory scan does not prove absence of all vulnerabilities or prior compromise. Hosting logs, access history, real TLS/SMTP delivery and operational monitoring must be reviewed in the deployment environment.
