# Security and deployment

Report security issues privately to the FG-TIDA secretariat (`tsbfgtida@itu.int`). Do not put passwords, reset links, authenticator seeds, personal records or unredacted logs in public issues.

Use the production deployment under `deploy/`, not the inherited IETF development/Kubernetes examples. Follow [the launch checklist](deploy/LAUNCH-CHECKLIST.md). Production uses HTTPS, secure cookies, an explicit domain and administrator allowlist, administrator MFA, private backend services and a restricted application database role. SMTP remains disabled until authorized and tested with ITU.

Private environment files, mounted secrets, database exports, uploads and detailed audit reports must remain outside Git. The release export contains password hashes, personal data and authenticator seeds: encrypt it and keep its decryption key separately. Never reuse development credentials in production. Rotate any genuine credential that was exposed previously; deleting a source line does not revoke it or remove history.

## Source scanning

The repository retains its upstream IETF history and synthetic tests/examples. `.gitleaksignore` lists only individually reviewed fingerprints from those inherited files: test tokens, documentation placeholders, a development account-migration example key, an expired synthetic NomCom key/certificate, Kubernetes examples, and a false positive on a migration's permission-field default. They are not production FG-TIDA credentials. No entire directory or secret rule is suppressed. New or moved findings must be reviewed; never add a live credential to the baseline. Historical findings do not establish that a credential is safe to reuse.

Before publishing, scan the tracked source snapshot, compare it against private local secret values, run dependency audits and build/test the release image. Keep the private audit evidence separately. Dependency and OS advisories change: repeat scans for every image rebuild and at scheduled maintenance. An advisory-free scan does not prove absence of vulnerabilities or past compromise.
