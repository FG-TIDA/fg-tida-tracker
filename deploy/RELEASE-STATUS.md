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

## Launch blockers

1. **OS-library advisories:** the container scan flags high-severity Expat findings [CVE-2026-117610](https://security-tracker.debian.org/tracker/CVE-2026-117610) and [CVE-2026-118370](https://security-tracker.debian.org/tracker/CVE-2026-118370), and zlib [CVE-2026-85091](https://security-tracker.debian.org/tracker/CVE-2026-85091). The vendor currently lists the stable Debian 13 package versions as vulnerable; fixes are listed in unstable. Do not mix unstable packages into the production image as an automatic workaround. Rebuild with vendor-supported patched packages or have the selected hosting/security team validate a patched supported runtime. Additional vendor-tracked medium/low findings remain in the private scan report; they were not blanket-suppressed.
2. **Hosting:** the owner must provide the host, HTTPS domain, database connection and trusted-proxy details. Configure network access, encryption, backups, restore testing, monitoring and host-account MFA. Shared storage must be integrated and tested before using multiple app instances.
3. **Administrator access:** enroll the real administrator's authenticator and save recovery codes before public launch. Use a production password distinct from development and confirm the recovery email is a mailbox under the owner's control. Re-export if enrollment or records change after this snapshot.
4. **ITU SMTP:** delivery is intentionally disabled. Welcome/reset/contribution mail cannot be delivered until ITU authorizes SMTP. Configure recipients and the scheduled outbox job, test real delivery, then run the final publication gate.

Use [the launch checklist](LAUNCH-CHECKLIST.md) for the operator steps. Source publication does not deploy the website or transfer its database to GitHub.
