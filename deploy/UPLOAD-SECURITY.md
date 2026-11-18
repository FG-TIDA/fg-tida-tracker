# File scanning on Render

Uploads from ordinary users and automated integrations pass structural checks
and a private ClamAV scan before conversion or publication. Documents, source
packages and hosted media are covered. Existing
originals, alternative formats, GitHub artifacts and session archives require a
successful scan before download, unless a superuser has approved the specific
antivirus finding for those exact bytes as described below. Structural checks
always apply. Contribution emails contain links only.
Accounts can make up to 20 submission or revision attempts per 15 minutes to
limit repeated expensive file checks.

### Administrator upload policy

As requested, files actually uploaded by an authenticated, active staff
administrator are exempt from antivirus scanning. They still pass file-type,
macro/active-content, archive and resource-limit checks before conversion or
publication. The admin list labels them **Administrator trusted — antivirus
skipped**, with an audit event identifying the uploader; this does not certify
them as antivirus clean. Administrators are responsible for checking their
upload sources and protecting their accounts/devices.

The exemption is scoped to exact uploaded bytes and locally generated,
structurally validated derivatives such as the converted RST ZIP. Merely
opening an admin page, editing metadata, fetching GitHub content, or running
a scheduled task does not exempt unrelated files. Scheduled antivirus scans
skip records with this administrator provenance. Changed bytes from ordinary
users remain subject to scanning. Files uploaded before this policy was
implemented are not automatically reclassified based on guesses about their
origin. The existing reviewed exception flow remains available for those.

See [Backend contribution entry and Word conversion](CONTRIBUTION-ADMIN.md).

## Create the private scanner first

1. In Render, choose **New → Private Service** and connect
   **FG-TIDA/fg-tida-tracker**, using the reviewed production branch.
2. Choose Docker, build context `.`, and Dockerfile `deploy/clamav/Dockerfile`.
3. Use the same region and private network as the Datatracker web service.
4. Choose an instance with 4 GB RAM. ClamAV documents at least 3 GiB, with 4 GiB
   preferred. This is an additional paid service. Review Render's displayed
   price before creating it.
5. Add a 2 GB persistent disk mounted at `/var/lib/clamav` for signature databases.
   This disk holds definitions, not uploaded contributions. Check its usage after
   the initial download and during updates; increase it before sustained usage
   reaches 75%. Disk size can be increased manually but cannot be decreased.
   Keep the official image's FreshClam updater enabled. Do not set environment
   options that disable FreshClam or ClamD.
6. Leave Docker Command empty. The scanner listens on port **3310**. Keep it
   private. A domain, public URL and DNS changes are unnecessary.
7. Deploy and wait for the logs to confirm that the databases have loaded and
   ClamAV is ready. The initial signature download can take several minutes.
8. Copy the internal hostname from the service connection details. Use only the
   hostname below, without a scheme, port or path.

The image follows ClamAV's 1.4 LTS feature line. Rebuild it for security patches,
and upgrade the feature line before its support ends. FreshClam updates
signatures independently of application deployment.
The 1.4 LTS line currently has security-patch support until 15 August 2027;
plan a supported-version upgrade before then. Rebuild the scanner image for
new engine/operating-system patches: signature updates do not install these.

## Configure the Datatracker web service

In **Web service → Environment**, set the following.

```ini
FGTIDA_UPLOAD_SCAN_REQUIRED=true
FGTIDA_CLAMAV_HOST=YOUR_PRIVATE_SCANNER_HOSTNAME
FGTIDA_CLAMAV_PORT=3310
FGTIDA_QUARANTINE_DIR=/data/quarantine
```

Use the existing `/data` disk for quarantine, outside static, media and document
directories. The private queue holds at most 250 MB. Temporary files expire
after seven days. Files that pass leave quarantine; rejected and structurally
blocked upload bytes remain private for this limited review window. No raw
quarantine download is exposed by the admin interface. A failed new submission
does not reserve its full form metadata: contributors should retain their
original and resubmit after a reviewed recovery. Existing archived documents
and their metadata are not deleted by quarantine expiry.

Keep the existing pre-deploy migration command. Deploy the reviewed web service
commit after the scanner is ready. Production enables required scanning by
default. Until configured, new files stay private, and existing unchecked files
have unavailable previews/downloads.

## Verify the scanner and check existing files

Open **Datatracker web service → Shell** and run the connection and detection test.

```sh
python ietf/manage.py scan_fgtida_files --check-scanner --skip-checks
```

This checks signature freshness and sends the standard harmless antivirus test
pattern plus a clean control only to your private scanner. The pattern stays in
memory and is never published or emailed.
Every new antivirus scan repeats the detection and clean-control self-tests
before trusting the real file's verdict. A failed test, incomplete protocol
response, unavailable scanner, or definitions older than 72 hours blocks the
new scan. The test demonstrates that the integration works, not that every
future malware sample will be detected.

Then scan the existing files.

```sh
python ietf/manage.py scan_fgtida_files --limit 200 --skip-checks
```

Repeat if the collection has more than 200 unchecked files. Review held or
rejected files under **Admin → File scans**. Older PDFs with automatic actions
or embedded files may need to be exported as passive PDFs and uploaded again.
Work items, archive contents, versions and approvals are preserved.

Verify an existing preview and download, submit a valid test package, and check
its scan result. Confirm that contribution notification emails have record links
and no file attachments.

The existing single-instance Render supervisor starts file maintenance when the
scanner hostname is configured. It checks up to 20 files per cycle, retries
private files after outages, and refreshes decisions after 24 hours as it works
through the collection.
It also runs the detection/control health command approximately every 15
minutes. On failure it logs `SECURITY: scanner self-test failed; file maintenance
is held.` and retries on a later cycle. Previously checked files retain their
recorded decisions; a newly rejected checksum loses public access unless a
reviewed exception covers that exact file, format and unchanged finding.
Enable Render service failure notifications and review both services' logs.
If you use a log-alert service, alert on this `SECURITY:` failure message and
FreshClam update failures, not on the expected EICAR test detections. No
external log-alert service is configured automatically by this repository.

## Review held files

**Admin → File scans** shows filenames, SHA-256 checksums, permitted formats,
scan status, attempts, engine and reason. Administrators can retry quarantined
files or remove held files while retaining their audit records. Scan results
cannot be marked clean manually.
An explicit superuser false-positive exception can allow access while retaining
the scanner's rejected verdict. It is shown separately in the admin list and
the record's read-only exception details; it never changes the verdict to clean.
Open a record to see its read-only history of scans, reviewer notes, results,
engine versions, removal and expiry. Only superusers can view or review these
records. This is an application audit trail, not tamper-proof storage against
someone with direct database access.

When scanning is unavailable during a new submission, its file is held privately.
The contributor must retry the submission after scanning is restored because
the original form's metadata has not been submitted. Queue processing only scans
files. It never creates contributions by itself.

For a suspected false positive:

1. Keep the file blocked. Note its SHA-256 and detection name in **Admin → File
   scans**. A structural rejection or scan limit is not proof of a malware
   infection. Do not disable the checks or broadly whitelist a signature.
2. Confirm that FreshClam updated successfully and the engine is patched. Check
   the detection name against ClamAV's guidance. Seek a second private review
   when necessary. Do not send unpublished contributions to public scanning
   sites or to ClamAV without the contributor's authorization.
3. In **File scans**, select the record and choose **Review and retry checks for
   selected files**, then **Go**. Enter an investigation reason (10 to 400
   characters) and click **Run security checks again**. Review at most ten
   files at a time. The action preserves the prior result and records the
   reviewer, time and reason. Retained originals are rescanned; no override to
   clean is offered. If the new scan still reports an issue, it stays blocked
   unless the explicit exact-file exception procedure below is completed.
4. If held bytes expired, ask the contributor to retry after this reviewed
   action. For an existing archived file, an operator can instead run a
   targeted rescan below. Replace the placeholder with the record's full
   lowercase 64-character checksum. This rescans only matching bytes and still
   requires both antivirus and structural checks to pass.

```sh
python ietf/manage.py scan_fgtida_files --sha256 CHECKSUM_FROM_ADMIN --force --reason "Reviewed detection after signature update" --skip-checks
```

5. Verify the resulting status, history and document access. For previously
   unsubmitted forms, the contributor must submit the form again after checks
   pass; recovery does not create a contribution or send contribution mail.

Use **Remove selected held files from private quarantine** to discard retained
upload bytes; the blocked decision and the history remain. Quarantine expiry
also preserves the scan decision and history.

## Approve or revoke a reviewed false-positive exception

Use this only after investigating the exact file. Knowing the sender does not
prove that their device, account or attachment is safe. The superuser accepts
responsibility for allowing access despite the antivirus finding.

1. Open **Admin → File scans** and select **one** record with a rejected
   antivirus finding. Choose **Approve a reviewed antivirus false positive
   (one file)**, then **Go**.
2. Review the displayed filename, SHA-256 and detection. Enter a 20–400 character
   investigation reason, confirm your administrator password, and check the
   explicit acknowledgment that the file may become readable/downloadable.
   Complete the review within ten minutes; a changed finding requires reopening
   the action. A superuser using only external sign-in must configure a usable
   local password before using this action.
3. Click **Approve this exact-file exception**. A fresh scan must reproduce the
   reviewed finding with working self-tests and fresh signatures. The held
   bytes must match the checksum and pass structural validation. Scanner errors,
   limits, encryption, active documents and other structural rejections cannot
   be overridden. If the fresh scan is clean, no exception is needed.
4. Check **Active manual exception** and the read-only history. An exception
   applies only to the exact checksum, inspected format and finding. It is not
   an uploader, filename or signature allowlist. Different bytes, edited revisions,
   additional formats or converted source packages require their own checks.
   No failed submission form is automatically created by approval; contributors
   must resubmit it when necessary.
5. To withdraw approval, select the record and choose **Revoke manual antivirus
   exceptions for selected files**, then **Go**. The reviewer and revocation are
   recorded, and subsequent application requests are blocked if the rejected
   finding remains. Already downloaded copies cannot be recalled.

Approved exceptions continue through periodic rescanning. A changed finding,
new structural failure, or a true clean verdict revokes the earlier exception.
Quarantine expiry/removal preserves the scanner verdict, exception and history;
it removes only privately held bytes. If originals have expired before approval,
rescan the archived file or ask the contributor to resubmit so the exact bytes
can be checked. Blocked files are not exposed for browser review before approval.

To rescan the whole collection during planned maintenance, use the following.

```sh
python ietf/manage.py scan_fgtida_files --force --limit 10000 --skip-checks
```

If the scanner fails, restore its private connection, memory or signature
updates while keeping required scanning enabled. Public GitHub references lead
to external repositories, whose own downloads are controlled by GitHub.

## Images in source ZIP previews

Scanned RST ZIP packages display referenced PNG, JPEG, GIF and SVG images
inline, including images extracted during Word conversion. Use RST `image`
or `figure` directives with paths relative to the chapter containing them.
Nested folders and image substitutions are supported. On smaller screens,
images scale to fit the page.

Images are read from the checked ZIP through protected application URLs;
the ZIP is not extracted into a public directory. Public document URLs pin
the image to a document revision. Private contribution previews keep their
existing submitter/admin access restrictions. Each image request rechecks the
archive's scan status, so an unchecked or subsequently blocked archive cannot
be accessed using an earlier image URL. SVG responses use a sandboxed content
security policy and cannot run scripts or fetch external resources.

Remote images are not loaded. Draw.io, Mermaid and PlantUML source files are
preserved as supporting files but are not executed. Export a diagram to PNG,
JPEG or SVG and reference that image in the RST source for an inline preview.
Existing scanned ZIPs need no re-upload and no new database migration.

## References

- [OWASP upload guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)
- [ClamAV Docker operation and memory](https://docs.clamav.net/manual/Installing/Docker.html)
- [ClamAV support policy](https://docs.clamav.net/faq/faq-eol.html)
- [ClamAV scan alerts and false positives](https://docs.clamav.net/faq/faq-scan-alerts.html)
- [Render private services](https://render.com/docs/private-services)
