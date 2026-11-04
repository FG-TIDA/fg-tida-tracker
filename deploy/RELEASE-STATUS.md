# Production preparation — 3 November 2026

The FG-TIDA application and private data transfer are prepared for hosting integration. **This is not approval to expose the site publicly.**

Verified locally:

- FG-TIDA-only export and isolated restore: 89 documents, six meetings and 29 referenced files; no unrelated IETF documents or meetings. Account and membership data remain private.
- Full 52-test FG-TIDA regression suite passed. Additional SMTP preparation and deferred-outbox tests passed, including WG/Incubation routing and attachments, reset priority, expired messages and retry limits.
- Both frontend bundles built. Python and JavaScript dependency audits report zero known application-package advisories after remediation.
- The staged source passes secret scanning with only the individually documented inherited test/example fingerprints. Private credential values, the actual administrator identity and database exports are excluded. The inherited database dump was removed from the current source tree; Git history was retained.
- The encrypted data-transfer package passed decryption, checksum and tampering-rejection checks. The key is kept separately. Detailed exports and audit reports are not in Git.
- A disposable production-mode restore passed HTTPS-page, restricted-database-role, MFA-gate, signed-in-contribution, disabled-SMTP and PDF-rendering checks. Its synthetic test authenticator is not the real administrator's enrollment.

## Render preparation — 4 November 2026

The [single-instance Render profile](RENDER.md) now supports a maintenance-only
first deployment, static-file serving, a local shared Memcached process, Render
secret-file permissions, PostgreSQL health checks and a verified artifact restore.
It deliberately disables Celery publishing rather than silently using a fake
broker. No additional Redis service or dedicated worker is needed for the current
FG-TIDA outbox workflows.

The production image built successfully. Twelve focused security/Render tests
passed in both development and the final production image. A disposable
PostgreSQL 18 instance accepted all migrations and the 648-record FG-TIDA fixture;
29 artifacts, 89 public documents and six meetings were verified. Normal HTTPS
pages, registration forms, static assets, canonical redirects, private-path
boundaries, WG1/WG2/WG3/Incubation contributions, the agenda and PDF rendering
passed. Administrator MFA and final SMTP gates rejected incomplete setup as
expected. Test authenticators existed only in the isolated test database.

The staged-source scan found no leaked secrets; a separate check found no
exported account-password hashes or actual administrator username. Exports,
synthetic test credentials and local audit files are not part of the commit.
These checks do not clear the OS advisories or complete the live Render/ITU
configuration. Deploy `bootstrap` first, not the full public website.

## Redeployment fixes — 4 November 2026

- Removed the unused API router, personal API-key routes and their menu entries.
  Direct GET/POST requests remain unavailable to anonymous and signed-in users.
  This also removes the inherited RFC API schema warnings from deployment checks.
- Startup initializes empty persistent upload directories without replacing files.
- Added explicit `FGTIDA_HSTS_INCLUDE_SUBDOMAINS` opt-in. Browser preloading stays
  off in accordance with current hstspreload.org guidance; only optional W021 is
  documented as an intentional Render policy exception. Other security checks
  remain enabled.
- Added an atomic email-column repair for fresh Django 5 schemas. It preserves
  case-insensitive identity, related foreign keys and account/MFA data, and refuses
  case-colliding addresses without deleting or merging identities. Existing
  application code already expects citext. Run migrations in Render's pre-deploy
  command after taking a database backup; do not re-import the release fixture.
- All 67 FG-TIDA/Render regression tests passed. The final image built, model-state
  checking found no missing migrations, and a populated PostgreSQL 18 upgrade
  preserved users, password hashes, MFA and email values. Collision rejection,
  citext foreign-key types, directory setup, HTTPS headers, removed APIs and both
  deployment/publication checks passed. The full production deployment check
  reports no issues with two documented silenced checks.

These fixes do not close the separate OS-library, SMTP or live backup-validation
launch requirements below. See [Render redeployment instructions](RENDER.md).

## Browser and native-library security update — 4 November 2026

- Added an enforced HTML Content Security Policy: same-origin scripts, no inline
  script handlers or eval, restricted form targets, and no embedding. Permissions
  Policy disables camera, microphone and location APIs unused by this website.
  PDF and media streams retain their native response handling.
- Built patched Expat 2.10.0 and zlib 1.3.2 (Debian source revision -4, including
  the overflow fix) against the stable Debian base. Python's separately bundled
  Expat extension is rebuilt against the fixed shared library too. No unstable
  binary repository is added to the runtime. The [backport recipe](NATIVE-SECURITY.md)
  is temporary maintenance until official stable images contain these fixes.
- All 69 FG-TIDA regression tests passed. Upstream Expat tests and 418 CPython
  XML/compression tests passed (seven upstream tests skipped). The final image
  passed deployment/deferred-SMTP gates, PDF rendering, XML/DOCX extraction and
  malicious external-entity rejection. Private HTTPS browser checks passed for
  account forms, authenticated admin add forms and agenda expansion; the browser
  rejected a deliberately injected inline script.
- A full PostgreSQL 18 dump restored into a separate disposable database with
  290 tables matching, including account/password/MFA data and authenticator
  decryption. This proves the local recovery procedure, not the current live
  Render backup. The [operator recovery checklist](BACKUP-RESTORE.md) requires
  verification of an actual production export and the corresponding disk copy.
- The publication source passed redacted secret scanning. Private exports,
  authentication records, credentials and test sessions remain excluded.
- The fresh image scan retains 44 raw vendor findings (three high, one medium,
  40 low). The three high rules still match every version on Debian 13, including
  the fixed backports installed here. Exact versions, patch/test evidence and the
  tar finding's applicability are documented in [native security](NATIVE-SECURITY.md).
  Findings were not globally suppressed and a zero-finding audit is not claimed.

## MFA redirect repair — 4 November 2026

Only published page navigations can now set the MFA return destination;
background requests cannot replace it. Stale, unpublished or unsafe destinations
fall back to the homepage. FG-TIDA's base template no longer requests the unused
IETF status/menu endpoints. All 71 FG-TIDA regression tests pass, including
password login followed by a real test authenticator code, intervening background
requests and arrival at the admin dashboard. Existing accounts, enrollments and
recovery codes are preserved.

## Remaining publication and operator requirements

1. **OS-library monitoring:** the Expat and zlib fixes above address the three previously identified high-severity code flaws. Retain raw scanner results and review vendor-tracked findings; an unbounded stable-distribution advisory can continue flagging a project backport. Do not globally suppress packages or claim a zero-finding audit. Replace the temporary backports with official stable updates when available.
2. **Render operations:** verify live database and disk backups, recovery, monitoring, external database access restrictions and hosting-account MFA. Shared storage must be integrated and tested before using multiple app instances. ITU/Cisco must review the separate domain block; code and certificate changes cannot authorize network access.
3. **Administrator access:** preserve the existing account and MFA enrollment. Verify recovery codes are stored securely, the production password differs from development and the recovery mailbox belongs to the owner. A code redeployment must not recreate accounts or re-import the initial fixture.
4. **ITU SMTP:** delivery is intentionally disabled. Welcome/reset/contribution mail cannot be delivered until ITU authorizes SMTP. Configure recipients and the scheduled outbox job, test real delivery, then run the final publication gate.

Use [the launch checklist](LAUNCH-CHECKLIST.md) for the operator steps. Source publication does not deploy the website or transfer its database to GitHub.
