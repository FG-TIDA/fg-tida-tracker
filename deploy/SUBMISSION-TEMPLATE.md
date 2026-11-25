# Specification-template checks and existing contributions

New NWI submissions through GitHub snapshots, RST ZIPs and Word uploads must
complete the front matter specified in the
[FG-TIDA source template](https://github.com/FG-TIDA/spec-template/blob/main/src/doc/index.rst):
an actual document title, then Abstract, History, Authors, Keywords and Foreword.
History must contain an X.Y version. Replace example text and placeholders in
all RST chapters. Place figures, logos and cover tables in the body rather than
ahead of the front matter. Word authors must use heading styles so conversion preserves sections.
Uploaded configuration is never executed. These checks establish structural
completeness, not technical approval, authorship or malware safety. Antivirus,
file checks and Word conversion review remain separate requirements.

Errors appear on the submission form before creating the contribution, public
document or notification. GitHub revision submissions receive the same checks.
Existing published records are not silently withdrawn or rewritten on deployment.

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
