# Production backups and recovery

The release fixture is a transfer format, **not a complete production backup**.
It deliberately excludes some authentication/permission records. Never reload it
over a running production database as a backup or a code-update step.

## What must be protected

- The entire Render PostgreSQL database, including accounts, MFA devices,
  permissions, contribution uploads, session attachment chunks and mail outbox.
- The persistent `/data` disk, including documents, proceedings, floor plans,
  charters and photos. Static files can be rebuilt, but user uploads cannot.
- The existing Django signing key and database credentials, stored separately
  in the team's approved secret manager. Rotating the Django key without a
  migration/recovery plan can invalidate signed links and encrypted MFA data.

Restrict backup access: these are private personal data and authentication
records. Never place dumps, media archives, keys or exports in GitHub, tickets
or publicly accessible URLs. Use encrypted independent storage with retention
and deletion policies agreed by the owner.

## Render operator checklist

1. Open the production **PostgreSQL → Recovery** page. Confirm the plan's
   point-in-time recovery window. Create a logical export and save the export
   privately. Provider retention and recovery windows depend on the plan; check
   the dashboard rather than assuming they are enabled indefinitely.
2. Before a risky change, put the web service in `bootstrap` maintenance mode
   and wait for in-flight requests to finish. This prevents the database and
   disk copy from representing different versions of an uploaded document.
   Run no migrations or imports during the copy. Do not change credentials or
   remove the attached disk.
3. Create/download a current full PostgreSQL export. Copy `/data` privately
   using the existing authenticated SSH connection, preserving relative paths.
   Do not expose a download endpoint on the website. Record UTC backup time,
   PostgreSQL major version, deployed Git commit and SHA-256 checksums in a
   manifest. Exit maintenance after both copies complete.
4. Keep an independent encrypted copy, with its encryption key stored separately.
   `release_archive.py` can encrypt a locally assembled backup archive using
   authenticated AES-256-GCM. It does not collect the database/disk for you and
   does not replace scheduled backups. Its input must contain the **full** dump,
   disk copy and manifest; do not substitute the selective release fixture.
5. Enable backup-failure alerts and assign an owner. Monitor both database and
   disk capacity. Agree recovery-point and recovery-time targets; for example,
   losing at most one day of changes requires at least daily independent copies,
   plus copies before changes. More frequent changes may require a shorter gap.

## Prove restoration before public launch

Use a new isolated PostgreSQL **18** database and separate disk. Never restore a
test backup into production or connect a test app to the production database.
Use a PostgreSQL 18 client (`pg_restore`/`psql`); an older client is inappropriate
for an 18 dump. Inspect the export's format before selecting the restore tool.
Restore with errors fatal and without restoring provider ownership/ACLs into a
new restricted application role. Follow Render's current export-format guide.

Verify checksums before restoring. Restore the matching disk copy and use a
test-only HTTPS origin. Disable SMTP and every scheduled job so a restored
outbox cannot send duplicate real emails. Keep the restored service private.
Use separate database credentials and a test origin. To verify decryption of
restored MFA devices, the isolated recovery app needs a protected copy of the
original Django signing key from the secret manager. Do not print it, include it
in the database/media archive, or expose this recovery app publicly. A different
signing key cannot prove recovery of encrypted authenticator data.

Check account and MFA counts; sign-in/MFA using an authorized test account;
document and agenda counts; document/PDF/floor-plan downloads; uploaded session
recording playback and byte-range seeking; admin permissions; contribution
attachments; database foreign-key integrity; and the full deployment and
deferred-SMTP publication gates. Compare these with the backup manifest. Record
restore duration and failures. Destroy the isolated clone after verification.

A successful local synthetic restore proves the procedure, not the existence
or recoverability of today's live Render backup. Record a successful restore of
an actual production export before considering this requirement closed.

References: [Render PostgreSQL backups](https://render.com/docs/postgresql-backups),
[persistent disks](https://render.com/docs/disks),
[PostgreSQL pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html).
