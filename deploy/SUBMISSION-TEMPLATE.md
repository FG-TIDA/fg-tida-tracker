# Specification-template checks and existing contributions

New NWI submissions through GitHub snapshots, RST ZIPs and Word uploads must
complete the front matter specified in the
[FG-TIDA source template](https://github.com/FG-TIDA/spec-template/blob/main/src/doc/index.rst):
an actual document title, then Abstract, History, Authors, Keywords and Foreword.
History must contain an X.Y version in its Version column or an explicit Version
field. Version `0.0` is valid for an initial draft, and its approval date may be
blank. Word-converted grid/simple tables are recognized; no approval date needs
to be invented. Replace example text and placeholders in
all RST chapters. Place figures, logos and cover tables in the body rather than
ahead of the front matter. Word authors must use heading styles so conversion preserves sections.
Uploaded configuration is never executed. These checks establish structural
completeness, not technical approval, authorship or malware safety. Antivirus,
file checks and Word conversion review remain separate requirements.

Errors appear on the submission form before creating the contribution, public
document or notification. GitHub revision submissions receive the same checks.
Existing published records are not silently withdrawn or rewritten on deployment.

## Submit a revised ZIP or Word document

After deployment, the original submitting account can sign in, open
**Documents → Submit NWI(New Work Item)**, select its existing item under
**My submissions**, and click **Submit a new version**. The item must still be
awaiting review in Submitted state. An authorized editor can assist with records
entered on someone else's behalf. Adopted work uses its WG/repository workflow.

1. Choose **RST source ZIP** or **Word (.docx)**. GitHub revisions remain available
   through the existing repository/commit route. No additional upload format is added.
2. Update the document's History to a higher X.Y version than the latest archive.
   Enter the same version on the form. Initial version `0.0` is valid for new
   documents; an unapproved draft's approval date may be blank.
3. For RST, upload the ZIP with all referenced assets and enter the full path to
   its main RST file inside the ZIP. The maintained archive includes the reviewed
   specification build scaffold. For Word, upload `.docx`, use heading styles and
   embedded figures, and leave the entry-document field empty.
4. Leave GitHub-only fields blank for uploads. Confirm publication and select
   **Archive version**. Template and file-security checks run before archiving.
5. Check both the contribution preview and public version page. For a Word
   revision, download the current Word original, compare its text/tables/figures
   with the conversion and confirm conversion accuracy before adoption. Each
   new Word revision needs fresh verification.

The original submission bytes and route remain recorded. Revised originals are
retained per version. Raw revised ZIP uploads are private to the submitter and
authorized contribution/document editors: a repository ZIP can contain files
outside the selected documentation folder. The public ZIP contains only the
validated document folder and its supporting files. Revised Word originals are
available after security checks. Earlier archives remain available, and the existing work
item, numbering, submitting account, meeting, status and deliberate metadata
overrides are preserved. The maintained source used for preview/adoption follows
the latest revision. Do not create a duplicate NWI. The original meeting deadline
still governs new submissions; revising an existing Submitted item does not
create another submission or change its selected meeting. Revision archiving
does not send another new-submission email.

The normal Render pre-deploy `migrate --noinput --skip-checks` command applies
the format-visibility and private-original archive migrations (`0048` and `0049`). Existing public alternatives keep
their visibility. No new credentials or external service configuration are needed.

## Adapt the production database after deployment

The local database and Render database contain different records. Deployment
updates the code; it does not copy edited local contributions to Render.

1. Back up the Render database and persistent document storage.
2. Deploy the commit on Render. Keep the normal migration pre-deploy command.
3. Open Render → Datatracker web service → Shell. Preview the plan without
   changing contributions or document versions (file checks may record scans):

   ```sh
   python ietf/manage.py fit_fgtida_templates --skip-checks
   ```

   The report uses contribution database IDs, not public sequence numbers or
   contact details. `already_conforming` needs no change. `ready` can be fitted
   without inventing missing information. `needs_review` means an editable draft
   can be prepared but missing information must be completed. `manual_review`
   explains missing files, source-management restrictions or other problems.

4. To inspect draft ZIPs for one record, use a private directory outside public
   media storage (the temporary directory disappears on redeployment):

   ```sh
   python ietf/manage.py fit_fgtida_templates --contribution 22 --draft-dir /tmp/fgtida-template-review --skip-checks
   ```

   Replace `22` with the database ID from the plan or admin contribution URL.
   Transfer drafts privately and inspect text, figures and tables. Draft filenames
   are created exclusively; choose a new directory when repeating this command.
   Drafts are not public downloads and are not emailed.

5. Apply a complete adaptation to that record:

   ```sh
   python ietf/manage.py fit_fgtida_templates --contribution 22 --apply --reason "Normalize specification front matter while preserving the original contribution." --skip-checks
   ```

   Omit `--contribution 22` to process all records. Only `ready` records are
   archived. Each change creates a new increasing X.Y version, records the
   adaptation in its History, retains earlier versions and the original upload,
   and preserves other download formats. Legacy cover material is kept outside
   the new front matter; its assets are retained. No mail or GitHub writes are performed.

6. Missing authors, abstracts or keywords are explicitly marked for review in
   drafts and block automatic application. Obtain the missing content from the
   contributor, edit the draft and use the existing reviewed revision process.
   Never tick the Word conversion verification checkbox without comparing the
   source and converted text, tables and images. A new adaptation clears that
   verification. Re-scan a derived file if its safety status requires it.
7. Open the contribution's public page and check front matter, version, preview,
   images and earlier archives. File safety controls still gate access.

## Provisional formatting of legacy uploads and author follow-up

If the secretariat deliberately wants to publish formatting adaptations before
authors supply missing details, the legacy-only option below permits that.
Missing text is explicitly marked rather than invented. The version history
records the adaptation. Only incomplete adaptations display the public author
verification notice and say they are not fully template-compliant. Adaptations
that passed the template checks do not display that notice.
New submissions and author revisions still require full template validation.

After backing up the production database/files and deploying, run in Render Shell:

```sh
python ietf/manage.py fit_fgtida_templates --apply --allow-incomplete-legacy --reason "Editorial template formatting of existing submissions; author verification pending." --contacts-csv /tmp/fgtida-template-review-contacts.csv --skip-checks
```

This applies eligible complete or incomplete legacy Word/RST adaptations. It
retains original uploads, source assets, original Word/PDF alternatives and prior
versions. Security failures and source-managed repositories are still excluded.
Repeating the command does not produce another adaptation for a version already
awaiting author verification. Use a fresh CSV filename for another run.

The CSV is private, created with owner-only permissions, and never printed into
the command's ordinary output. It lists contribution IDs/references, titles,
URLs, formatting issues, actual submitting-account names/emails and separately
the submission contact names/emails. Contact details are not inferred from a
document's author list. An empty account field can mean an admin entered the
submission for someone without an account; check the separate contact fields.
Source-managed and already-conforming records are not listed as badly formatted.
`manual_review` means inspection could not be completed, not a proven formatting
failure. `archived_provisional` and `awaiting_author_review` require author/editor
follow-up; no notification emails are sent automatically.

To view the contact list privately in your interactive Render Shell:

```sh
cat /tmp/fgtida-template-review-contacts.csv
```

Copy it to your private records or provide it privately to the secretariat for
follow-up. Do not commit the CSV, upload it as a public document, add it to a
deployment command or paste it into public issue/deployment logs. `/tmp` is
temporary, so save the report privately before redeployment. Public pages show
the editorial notice, not this contact list. A subsequent corrected upload is a
new revision; the original and adaptation remain in history.

## GitHub-managed contributions and use cases

The command does not overwrite automatically synchronized documents or catalogue
records. Edit their authoritative repository through a reviewed pull request,
then synchronize normally. A template-specific use-case form remains authoritative
until its editors approve a specification adaptation; the tracker must not replace
it with fabricated content. This command reports such records for source review.

The check profile is pinned in code (`spec-front-matter-2026-11-25`) and can be
updated deliberately if the official template changes. Validation makes no live
GitHub request during a file upload; a GitHub snapshot submission retains its
existing bounded download at the specified commit.
