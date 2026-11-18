# File scanning on Render

Uploads pass structural checks and a private ClamAV scan before conversion or
publication. Documents, source packages and hosted media are covered. Existing
originals, alternative formats, GitHub artifacts and session archives require a
successful scan before download. Contribution emails contain links only.
Accounts can make up to 20 submission or revision attempts per 15 minutes to
limit repeated expensive file checks.

## Create the private scanner first

1. In Render, choose **New → Private Service** and connect
   **FG-TIDA/fg-tida-tracker**, using the reviewed production branch.
2. Choose Docker, build context `.`, and Dockerfile `deploy/clamav/Dockerfile`.
3. Use the same region and private network as the Datatracker web service.
4. Choose an instance with 4 GB RAM. ClamAV documents at least 3 GiB, with 4 GiB
   preferred. This is an additional paid service. Review Render's displayed
   price before creating it.
5. Add a 2 GB persistent disk mounted at `/var/lib/clamav` for signature databases.
   Keep the official image's FreshClam updater enabled.
6. Leave Docker Command empty. The scanner listens on port **3310**. Keep it
   private. A domain, public URL and DNS changes are unnecessary.
7. Deploy and wait for the logs to confirm that the databases have loaded and
   ClamAV is ready. The initial signature download can take several minutes.
8. Copy the internal hostname from the service connection details. Use only the
   hostname below, without a scheme, port or path.

The image follows ClamAV's 1.4 LTS feature line. Rebuild it for security patches,
and upgrade the feature line before its support ends. FreshClam updates
signatures independently of application deployment.

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
after seven days, and checked files leave quarantine after scanning.

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

## Review held files

**Admin → File scans** shows filenames, SHA-256 checksums, permitted formats,
scan status, attempts, engine and reason. Administrators can retry quarantined
files or remove held files while retaining their audit records. Scan results
cannot be marked clean manually.

When scanning is unavailable during a new submission, its file is held privately.
The contributor must retry the submission after scanning is restored because
the original form's metadata has not been submitted. Queue processing only scans
files. It never creates contributions by itself.

For a false positive, update signatures and review the original. To rescan the
whole collection during maintenance, use the following.

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
- [Render private services](https://render.com/docs/private-services)
