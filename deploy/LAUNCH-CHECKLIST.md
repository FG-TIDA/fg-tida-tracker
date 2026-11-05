# Production handover

The application is prepared for a dedicated FG-TIDA database. The inherited IETF table names are the application's schema; imported IETF documents, meetings and accounts must not be loaded.

## Owner actions before opening the site

1. Choose the host, region and HTTPS domain. Supply those details to complete the provider-specific configuration. The Compose deployment is a single-server reference; its local assets volume cannot be shared by independent autoscaling instances. Autoscaling requires shared object storage and a tested storage backend first.
2. Transfer the private FG-TIDA fixture and assets through an encrypted channel. Never upload them to GitHub. Keep the export decryption key separately from the encrypted archive. Preserve the source until the restored database and files pass verification.
3. Install the separately generated Django, database-owner and application database secrets in the host's secret manager. Restrict their access. These are new credentials; do not reuse development passwords. A new production database receives these credentials at initialization. Changing secret files does not rotate passwords in an existing database.
4. Fill the private `production.env` using the example. Set the actual public domain, exact trusted TLS proxy CIDR and the approved administrator username. Run migrations, import the fixture and files, run `configure_fgtida`, collect static assets, and verify file checksums.
5. Enroll the administrator's authenticator before public launch. The development MFA pause never applies to production. Keep one-use recovery codes outside the server. Enable MFA on hosting, registrar and GitHub accounts; use SSH keys and restrict operator access. Never expose PostgreSQL or pgAdmin publicly.
6. In admin, enter WG1/WG2/WG3 contribution recipient addresses and FG management addresses for Incubation. Recipients are private configuration, not repository content.
7. Configure and test encrypted database plus artifact backups, a restore exercise, error/uptime monitoring, and alerts for storage and database capacity. Schedule `prune_authentication_attempts` daily. When SMTP is enabled, schedule `deliver_fgtida_mail` to retry queued mail. Retention and backup location need the owner's policy.
8. Run `check --deploy` and `check_fgtida_publication` in the real host environment, then test sign-up/login/MFA, reset, contributions and attachments, document revisions/downloads, meeting agenda time zones and admin edits over HTTPS.

Refresh OS packages with a fresh `docker compose -f deploy/compose.yml build --pull --no-cache` before the release scan; a cached rebuild of pinned base images will not fetch new security packages. Update and revalidate base-image digests as supported releases change. The current OS findings and evidence are recorded in [release status](RELEASE-STATUS.md).

## Enroll the administrator before the final export

Development deliberately pauses administrator MFA. To enroll on the local private tracker, restart only its Django server with the pause disabled (the container name below is the default development name):

```sh
docker exec fg-tida-datatracker-app-1 pkill -TERM -f '^python ietf/manage.py runserver 127.0.0.1:8001'
docker exec -d -e FGTIDA_DEV_ADMIN_MFA_PAUSED=false fg-tida-datatracker-app-1 sh -c 'python ietf/manage.py runserver 127.0.0.1:8001 --noreload > /tmp/fgtida-local-server.log 2>&1'
```

Open the tracker, sign in, and use **User → Two-factor authentication**. Choose a time-based account in your authenticator app, enter the displayed setup key privately, and confirm the code and your password. Save the recovery codes in a private password manager. Never put the key or codes in Git or chat. Produce a new encrypted database export after enrollment; the earlier preparation snapshot has no enrolled real administrator. The development pause can be restored afterward by starting the server without that environment override. Production always enforces MFA.

## SMTP configuration

Preparation does not require an SMTP hostname, account or password. Leave `FGTIDA_MAIL_ENABLED=false`. Mail stays in the database outbox; no real delivery occurs. New registrations remain pending email confirmation and cannot sign in until confirmed; existing accounts retain access. Confirmation, contribution and password-reset messages cannot be delivered yet. Expired links must be requested again after delivery is enabled. Test authorized ITU delivery before announcing public registration.

`check_fgtida_publication --allow-deferred-smtp` checks a private preparation environment; it is not final launch approval. The container's normal startup gate still requires mail delivery and administrator MFA. Do not bypass that gate to expose an untested release publicly.

After ITU approval: configure authorized sender, hostname, port and credentials; mount the SMTP secret with `compose.smtp.yml`; enable mail; send controlled welcome/reset/contribution tests before draining the queued outbox. Production web requests never wait for SMTP. Schedule `python ietf/manage.py deliver_fgtida_mail` every minute in the production environment: a database lock prevents overlapping jobs, expiring reset messages take priority, expired links are skipped, each run has a time/batch budget, and automatic retries stop after five failures. Contribution recipients are checked again at delivery; deactivated or removed recipients' pending notifications are cancelled. Review failed messages in admin before retrying manually. Confirm recipients and attachment handling. Run the final publication check again.

Freeze edits for the final cutover export, or take a consistent database-and-assets snapshot. Re-export if content or authenticator enrollment changes before launch.

## Repository and security

Only source, schema migrations and sanitized examples belong in GitHub. `.env`, secret files, database backups, user profiles, account hashes, authenticator seeds, contribution files and private audit reports are excluded. Keep secret scanning and dependency checks enabled. Review inherited upstream test/example fixtures separately from live secrets. Any genuine previously exposed credential must be revoked; deleting its current line does not remove Git history.

Security references: [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/), [OWASP secrets management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html).
