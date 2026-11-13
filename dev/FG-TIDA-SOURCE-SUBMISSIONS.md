# Source contribution submissions

The signed-in `/submit/` form now offers exactly three routes:

- GitHub: a public personal repository, full commit SHA, source folder and entry RST path. The fixed commit is downloaded without an App token. Only the selected documentation folder becomes the maintained snapshot.
- RST ZIP: preserve the uploaded ZIP and prepare a source snapshot from the entry document's folder and descendants.
- Word fallback: preserve DOCX, automatically convert with Pandoc into RST plus extracted media, and require contributor verification before adoption.

All routes retain the existing Submitted record, version 0.0, WG/Incubation routing, durable mail outbox, receipt and attachment delivery. Resend SMTP configuration and account/confirmation email flows are unchanged. Notifications remain subject to the existing mail settings and configured recipients. Tests use the in-memory backend, never real recipients.

The source package is the public document download. Original uploads and conversion details remain available to the submitter and authorized editors. Provenance records include file hashes, package hash and, for GitHub, the commit and repository. Earlier contributions retain the legacy method and remain readable/adoptable.

## Validation and conversion

Source ZIPs are limited to 10 MB compressed, 30 MB expanded and 500 entries. Paths, duplicate names, encryption and symlinks are checked. Each RST chapter is limited to 500 KB. Image/figure/include/literalinclude dependencies must resolve inside the selected source package. RST toctrees, citations and extension-specific semantics still require the official build/reviewer checks.

Uploaded build configuration is never run by the tracker. The reading preview renders chapters separately using the existing isolated RST renderer; it is not a full Sphinx build. Assets have separate authenticated download links. Only raster image types are served inline; SVG and other files are downloads.

Pandoc runs in its sandbox through a process with memory, CPU, output-file and wall-time limits. Linked external media and unresolved tracked changes are rejected. Complex drawings, tables, mathematics and references require contributor verification. Failed conversion creates no submission or mail. Corrections can be prepared and submitted again; the original archived submission is not overwritten.

## WG repository adoption

The existing provisioning job creates the official repository in the adopting WG organization. Its review PR imports the checked source package and assets under `src/doc`, while retaining the official template's Python configuration and workflows. Original source hashes and repository provenance are included in tracker metadata. Repository creation, state decisions and release publishing retain their existing gates. No GitHub writes occur during initial submission.

## Deployment

Rebuild the app image: `deploy/Dockerfile` and the development Dockerfile now install Pandoc. Apply migrations `0018_contribution_conversion_verified_at_and_more` and `0019_alter_outgoingmail_html_body`. The latter supplies an explicit empty HTML body for plain-text outbox messages, including on databases without a working column default. Restart the web and job processes against the same code and database. No Resend credentials or recipient settings need changing.

Local validation command (the inherited IETF runner is incompatible with Django 5.2):

```sh
python ietf/manage.py test ietf.fgtida.test_source_submissions ietf.fgtida.test_github ietf.fgtida.tests --testrunner django.test.runner.DiscoverRunner --keepdb --noinput
```

## Verification

The submission, GitHub and existing workflow regression suite passed: **96 tests** using Django's standard runner. This covers mail routing/retries and acknowledgements with an in-memory backend; no real messages or GitHub writes were made. Local migrations 0018 and 0019 applied successfully, and Django system checks reported no issues. Production deployment remains separate.

Final focused verification also passed: **15 tests** covering the latest source submissions, real Word conversion (including titles and embedded images), conversion verification, adoption source import and RST input/output document handling.
