# FG-TIDA: manual GitHub and Render activation

Follow these steps in order. Configuration changes are yours to make; this guide
does not imply that the live service or the other GitHub repositories were changed.
Keep ITU mail disabled until its authorized transport is supplied and tested.

## 1. Understand versions before deploying

- Initial submission/import: **0.0**. Subsequent admin uploads default to **0.1**,
  **0.2**, etc. In **Admin → Documents → select document**, upload a file and
  optionally enter **New file version**, e.g. **1.0**. The next automatic minor
  version is then **1.1**. Metadata-only edits do not create a version.
- A GitHub tag **v0.1** creates tracker version **0.1**; **v1.0** creates **1.0**.
  Use exactly two numeric components, without leading zeroes. A version must be
  greater than every archived version, including manual uploads. Because the
  original submission already owns **0.0**, normally make the first release **v0.1**.
- A main-branch build updates the working PDF without creating an archive version.
- Existing native archive IDs (`00`, `01`, etc.), storage filenames and old links
  remain valid. New X.Y links and download names use the public version labels.
  The migration adds mappings; it does not rename or overwrite uploaded files.
- The version selector highlights the selected archive in blue. The amber/cyan
  timeline follows recorded dates; colours alternate to distinguish versions,
  rather than asserting IETF draft expiry or FG-TIDA approval. Hover for timestamps.
- Type, authors/affiliations, version/date, last update, formats, category, WG,
  lifecycle, history, secretariat and linked GitHub release/commit are displayed.
  Edit metadata and author inlines in **Documents**. Unknown metadata is shown as
  not entered, not invented. IESG/IANA/RFC-editor fields are not FG-TIDA processes.

## 2. Back up, then deploy the tracker

Use a maintenance window for this first version-mapping migration. Before the
backup, open **Render → web service → Environment**, record the existing
`FGTIDA_RENDER_MODE`, set it to `bootstrap`, and choose **Save and deploy** using
the existing build. Wait until the site shows maintenance mode. This prevents
old application code accepting uploads while the X.Y mappings are backfilled.
Coordinate with editors and stop other import/write jobs during this window.

1. In Render open your PostgreSQL service. Create/download a backup using the
   backup/export option available for your plan. Also preserve a backup of the
   web service's `/data` disk; PostgreSQL alone does not contain all document files.
   Record the current successful deploy's commit and retain both backups privately.
2. Open **Render → fg-tida-tracker web service → Settings**. Confirm Dockerfile
   `deploy/Dockerfile` and the existing **Pre-Deploy Command**:

   ```sh
   python ietf/manage.py migrate --noinput --skip-checks
   ```

3. Under **Environment**, keep `FGTIDA_MAIL_ENABLED=false`; preserve the working
   database, HTTPS, domain, production secret and MFA settings. Leave the three
   GitHub feature flags below false during initial deployment.
4. Click **Manual Deploy → Deploy latest commit**. Check that the new commit
   containing this guide is selected. Wait for **Live**. An environment-only
   **Save and deploy** reuses an existing build; it does not fetch new code.
5. Open the web service's **Shell**, then run:

   ```sh
   python ietf/manage.py configure_fgtida_github --skip-checks
   python ietf/manage.py check_fgtida_publication --allow-deferred-smtp --skip-checks
   ```

   Do not run the full `manage.py check` while in `bootstrap`: the maintenance
   server intentionally does not start Memcached. A cache connection refusal in
   that mode does not mean the configuration/import commands failed.

6. After successful migration/configuration, return to **Environment**, restore
   `FGTIDA_RENDER_MODE=staged` while ITU mail is deferred, and **Save and deploy**
   the newly built release. Then verify the pages and MFA before reopening editing.
   Do not select normal `production` mode until its email/publication gates pass.

7. After the staged deployment is Live, open a **new Shell** for that running
   deployment and run:

   ```sh
   python ietf/manage.py check
   python ietf/manage.py check_fgtida_publication --allow-deferred-smtp --skip-checks
   ```

   Staged startup launches and supervises Memcached at `127.0.0.1:11211`.
   The full check must run here, rather than in bootstrap mode or the separate
   pre-deploy environment. Keep the pre-deploy migration's `--skip-checks` option.
   `FGTIDA_ENV` stays `production`; staged mode does not disable admin MFA or HTTPS.

Migrations need a database role authorized to create/alter the schema. If the
runtime role is intentionally CRUD-only, have the database owner arrange the
migration through your approved deployment role/process. Do not make the web
application role a PostgreSQL superuser or paste connection credentials into chat.

**Pass:** migrations complete, checks pass with SMTP explicitly deferred, admin
MFA still works, and document pages show X.Y versions and the timeline. Stop on
migration failure; retain the deployment logs. Do not reload an old fixture into
the live database, which could overwrite newer accounts and editorial changes.

## 3. Prepare GitHub ownership and templates

1. Use an owner account with MFA for **FG-TIDA** and each **fg-tida-wgN** organization.
   Grant routine editors repository access rather than organization-owner access.
2. Open **FG-TIDA/spec-template → Settings → General** and confirm **Template
   repository**. Repeat for **FG-TIDA/wg-charter-template**.
3. Keep the tracker App out of unrelated repositories. No personal access token
   is needed. Do not enter database credentials or ITU credentials into GitHub.

## 4. Register the GitHub App

1. GitHub profile menu → **Your organizations → FG-TIDA → Settings → Developer
   settings → GitHub Apps → New GitHub App**.
2. Choose a unique App name, e.g. **FG-TIDA Tracker Sync**. Homepage URL:
   `https://tida-datatracker.org/`.
3. Leave user OAuth authorization, device flow and setup callback unused. This
   integration acts as an installation, not on behalf of a participant's login.
4. Enable the webhook. URL:
   `https://tida-datatracker.org/integrations/github/webhook/`.
5. Generate a webhook secret with at least 32 random bytes using your password
   manager's secure generator. Save it privately; enter the same value in GitHub
   and the Render secret file in the next step. Do not use your login password.
6. Under **Repository permissions**, request **Contents: Read and write**,
   **Pull requests: Read and write**, **Issues: Read and write**, default
   **Metadata: Read-only**, and **Repository creation: Write**. Repository creation
   is an owner-approved permission; broad Administration/Workflows write is not
   needed for this implementation. If your interface/policy does not expose the
   creation permission, stop and review with the organization owner.
7. Subscribe to **Push** and **Issues** events. Keep SSL verification enabled.
8. For installation availability select **Any account**, because the WG
   organizations are separate accounts. This does not grant them access to your
   tracker: the backend only trusts administrator-configured organizations,
   installation IDs and repository identities.
9. Click **Create GitHub App**. Record the **App ID** (not the client ID). In its
   settings generate a **private key** and retain the downloaded PEM privately.
   Keep both PEM boundary lines and every line between them intact.

**Why:** the App has short-lived installation tokens and limited repository
access. The PEM stays on Render; it is never a public repository file.

## 5. Mount secrets and configure Render

1. **Render → web service → Environment → Secret Files → Add Secret File**.
   Filename: `github-app.pem`. Contents: the entire downloaded PEM.
2. Add another secret file, `github-webhook-secret`, containing only the same
   webhook secret entered in GitHub.
3. Add these environment variables using **Add Environment Variable**:

   | Key | Value |
   | --- | --- |
   | `FGTIDA_GITHUB_APP_ID` | Your App ID |
   | `FGTIDA_GITHUB_PRIVATE_KEY_FILE` | `/etc/secrets/github-app.pem` |
   | `FGTIDA_GITHUB_WEBHOOK_SECRET_FILE` | `/etc/secrets/github-webhook-secret` |
   | `FGTIDA_GITHUB_WRITE_ENABLED` | `false` |
   | `FGTIDA_GITHUB_INGEST_ENABLED` | `false` |
   | `FGTIDA_GITHUB_SCHEDULE_ENABLED` | `false` |

4. Save/deploy these configuration changes, then wait for **Live**. If you also
   need new code, use **Manual Deploy → Deploy latest commit** instead of assuming
   the environment-only deployment fetched it.

**Why:** separate flags let you test authentication/imports before allowing
repository creation, build uploads and unattended jobs. Do not paste secret values
in chat, screenshots, tickets or repository workflow files.

## 6. Install the App and map organizations

1. In the App settings choose **Install App → FG-TIDA → Install**. Select the
   known content/template repositories: themes, use-cases, meetings, marketing,
   wg1-ra-aai, spec-template and wg-charter-template.
2. Install in **fg-tida-wg0**, **fg-tida-wg1** and **fg-tida-wg2** too. Select their
   official existing repositories. The new repository-creation permission grants
   the creating App access to newly created repositories; verify this in the
   acceptance test rather than granting access to unrelated repositories.
3. For each installation open its configuration page. The number in
   `/settings/installations/NUMBER` is its **installation ID**. It differs between
   organizations and is not the App ID.
4. Sign into **tida-datatracker.org/admin/** with MFA. Open **GitHub accounts**.
   For each organization enter its matching installation ID; leave it enabled.
5. In **Working group configurations**, verify WG number, topic acronym, group
   and GitHub organization. These values determine official repository names.
   WG0's `uct` is an editable local shorthand; confirm it with the group.
6. In the App's webhook **Advanced / Recent deliveries**, inspect/redeliver its
   ping. A correctly configured ping returns HTTP 200 and `ready`. A 503 normally
   means the secret/configuration or deployed integration is missing; a 401 means
   the signature did not match. Never disable signature/SSL verification to fix it.

## 7. Review and import the public sources

After deploying the latest code, capture a non-secret, read-only baseline in
Render Shell:

```sh
python ietf/manage.py audit_fgtida_content --skip-checks
```

It reports the deployed commit, required migrations, archive/version gaps,
imported-repository counts and charter links. Run it again after the import.
No user accounts, recipient email addresses or credentials are printed. A missing
version label on an existing file is different from a document that has never
had an archived file. Do not fix either by replacing the entire production database.

Deployment/migrations and `configure_fgtida_github` do not download documents.
They create schema and editable source mappings. The local import is not copied
to Render. Run the one-time import below in **Render Shell** to populate the
production database and its own persistent disk. This inbound pull from public
repositories does not require enabling automatic sync or granting GitHub writes.
Keep write, ingest and scheduler flags false during the review. Anonymous GitHub
API calls are rate-limited; a configured App improves reliability but is not
required for a small public-source pull. Do not enable writes merely to import.

To check just the known charters first:

```sh
python ietf/manage.py sync_fgtida_github --repository fg-tida-wg1/wg1-cdra-charter --skip-checks
python ietf/manage.py sync_fgtida_github --repository fg-tida-wg2/wg2-ibea-charter --skip-checks
# After reviewing each plan, repeat it with --apply to import that charter.
```

If the summary says `manual/excluded`, the record is deliberately protected.
Review **GitHub sources** in admin before resuming it; never reset every source
to automatic merely to force an import. If imported file metadata exists but the
actual file is missing, check the persistent disk and restore its backup rather
than assuming a schema migration transferred the file.

1. In tracker admin open **GitHub repositories**. Review owning group, document
   type, mode, workflow path and allowed paths. Support files remain excluded.
2. In Render Shell run the read-only plan, review the counts, then apply:

   ```sh
   python ietf/manage.py sync_fgtida_github --skip-checks
   python ietf/manage.py sync_fgtida_github --apply --skip-checks
   ```

3. Browse document search, WG pages and meeting materials. Check a PDF/PPTX and
   a table-containing document. Existing manual edits/deletion exclusions are
   preserved. Imports do not email recipients about historic documents.
4. For an existing imported charter that should receive versioned build uploads:
   in **GitHub repositories**, set its mode to **Versioned work item** while keeping
   its correct document type/path; then **Work items → Add**, choosing that existing
   document and repository. Do not create a second copy of the charter.
5. In **Contribution recipients**, add each WG recipient/list address and the
   Incubation management address, mark active, and save. These private routing
   addresses are not imported from GitHub or displayed as public metadata.

**Pass:** a second import reports unchanged sources rather than duplicating files.
An admin-edited document remains paused, and a deleted source is not recreated.

## 8. Install build publishing using a pull request

### File-based templates, charters and deliverables

These document types use the same archive and X.Y version controls as other
documents. For a repository containing ordinary document files:

1. Open **Administration → GitHub repositories → select the repository**.
2. Set **Mode** to **Document files**, select its owning group, document type
   (`tida-template`, `charter` or `tida`) and category, and enter explicit document
   paths. Do not include repository support files such as HOWTO or CHANGELOG.
3. Review/apply the import in section 7. Each changed file becomes a new archived
   minor version; identical files do not create versions. This mode does not
   interpret release tags as version numbers. For tagged `vX.Y` PDF releases,
   use **Versioned work item** and complete the authenticated build setup below.
4. To automate file imports, complete the App/webhook and job-processing setup
   in sections 4–6 and 10. A GitHub push queues a catalog job; processing that job
   imports the changed approved paths. Merely deploying the tracker does not
   activate this connection. Test one change before enabling unattended jobs.
5. In **Administration → Documents**, admins can upload a replacement revision,
   edit metadata or delete the document. Editing an imported document pauses its
   source automatically; deleting it excludes that source so the next import
   does not recreate it. Resume an individual source deliberately under
   **GitHub sources → Sync mode → Sync from GitHub** when appropriate.

Archived file bytes remain immutable. Corrections are new revisions, preserving
links to previously cited versions.

### Input and output documents

After deploying migration `fgtida.0013_documentlisting`:

1. Open **Administration → Documents → Add document** (or edit an existing one).
2. Enter the identifier, title and owning group. Upload a **PDF or Word (.docx)**
   file using **Upload**; validated files use the existing persistent archive.
3. Set **Input / output list** to **Input document** or **Output document**.
4. Fill in **Document meeting**, **Document source** and optionally **Document
   creation date**. These populate the public table. The creation date stays
   fixed when uploading a new revision; the lists show newest entries first.
5. Leave **Show in public catalog** checked, add a history note, then save.
   **Documents → Input documents / Output documents** now shows the entry.
   Its name downloads the file; its title opens metadata and revision history.
6. Upload subsequent revisions on the same document and optionally specify an
   increasing X.Y version. To remove an entry from these lists without deleting
   its archive, set **Input / output list** to **Neither**. The normal document
   delete action remains available to authorized admins.

Do not re-import a local database fixture over production to add these lists.
The migration adds only the editorial metadata table, leaving existing data intact.

Do this in both templates and existing work-item repositories. Updating a template
does not modify repositories previously created from it.

1. Open the tracker repository's latest reviewed commit and copy its full SHA.
   This pins the publishing action to code you reviewed.
2. In the target repository navigate to **Code → .github → workflows → docs.yml**.
   Click the pencil, edit on a new branch, and open a pull request. Review before
   merging. Retain the existing HTML/PDF/Pages build steps.
3. Under `on.push`, retain `branches: [main]` and add `tags: ['v*']`.
   Keep `workflow_dispatch`. Ensure the **build job** has `contents: read` and
   `id-token: write`; the current Pages workflows already declare these globally.
4. Before the HTML/PDF builds add:

   ```yaml
   - name: Reproducible document timestamp
     run: |
       echo "SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)" >> "$GITHUB_ENV"
       echo "FORCE_SOURCE_DATE=1" >> "$GITHUB_ENV"
   ```

5. After **Include PDF in the site** and before **Upload Pages artifact**, add:

   ```yaml
   - name: Publish to FG-TIDA Datatracker
     if: github.event_name != 'pull_request'
     uses: FG-TIDA/fg-tida-tracker/.github/actions/publish-work-item@PASTE_FULL_REVIEWED_COMMIT_SHA
     with:
       pdf: target/latex/specification.pdf
       source: src/doc
       tracker-url: https://tida-datatracker.org
   ```

   Replace the SHA placeholder. The currently verified PDF paths are:

   | Repository | `pdf` input |
   | --- | --- |
   | FG-TIDA/spec-template | `target/latex/specification.pdf` |
   | FG-TIDA/wg1-ra-aai | `target/latex/specification.pdf` |
   | FG-TIDA/wg-charter-template | `target/latex/wg-charter.pdf` |
   | fg-tida-wg1/wg1-cdra-charter | `target/latex/wg-charter.pdf` |
   | fg-tida-wg2/wg2-ibea-charter | `target/latex/wg-charter.pdf` |

   If a future repository renames its LaTeX output, update this input accordingly.
   The workflow name/path must match the tracker repository record's
   `.github/workflows/docs.yml` exactly.
6. Under the existing `jobs.deploy` entry add `if: github.ref == 'refs/heads/main'`
   at the same indentation as `needs` and `runs-on`. Tags archive in the tracker;
   they should not attempt a GitHub Pages deployment restricted to the main branch.
7. Keep fork PRs outside publishing. Require PR review for `main`, protect `v*`
   tags against replacement/deletion, and restrict workflow changes to maintainers.
   Configure rules under **Settings → Rules → Rulesets**, subject to your plan's
   support for enforceable rules. Keep actor permissions sufficient for reviewed
   App metadata branches/PRs; the App never needs to bypass required main-branch review.

## 9. Connect metadata and test one work item

1. Choose a real Draft/Under-review work item for the test, or an explicitly
   approved integration-test item. Do not generate a misleading production release.
2. In **Admin → Work items**, select it and choose **Queue repository creation /
   metadata pull request**. Keep writes disabled until the mappings are reviewed.
3. Set `FGTIDA_GITHUB_WRITE_ENABLED=true` in Render and redeploy. In Shell run:

   ```sh
   python ietf/manage.py process_fgtida_github --limit 5 --skip-checks
   ```

4. Inspect **GitHub jobs** in admin. Confirm the repository is in the intended WG
   organization, then review/merge its tracker metadata PR. Incorporate the
   participant's actual draft through a reviewed PR; this content transfer is an
   editorial step. Merely pasting a personal draft-repository URL does not activate
   sync. Never push secrets or recovery information into the source tree.
5. Set `FGTIDA_GITHUB_INGEST_ENABLED=true`, redeploy, and merge a reviewed document
   change. In **GitHub → repository → Actions**, open the build. The publisher
   should report a working-copy update; the tracker history should identify its
   commit, while the archived version stays unchanged.
6. To archive, open the repository's **Releases → Draft a new release → Choose a
   tag**, create **v0.1** against reviewed `main` (or the next greater X.Y version),
   and publish. Confirm the tag-triggered Actions run succeeded. The tracker must
   show **0.1**, its recorded date, matching tag/commit, PDF, source TAR.GZ and
   manifest. Click 0.0 and 0.1 in both selector and timeline and check the files.
7. Change the tracker's lifecycle from Draft to Under review as appropriate and
   run the job command again. Verify its GitHub badge/managed label. Only editors
   should mark Published after the real WG decision and archived release.

**Pass:** main merges update only the working copy; a new tag creates exactly one
archive; retrying an identical release does not create another archive. No pending
job has an unexplained error. A label, issue closure or tag alone is not WG approval.

## 10. Enable unattended processing

1. After the acceptance test, set `FGTIDA_GITHUB_SCHEDULE_ENABLED=true` in Render.
   Save/deploy, wait for Live, then inspect **Logs** after the first minute.
2. Look for job completion/import summaries, then verify a newly queued job is
   processed without your running a Shell command. Job polling is approximately
   every minute; catalog polling approximately every 15 minutes plus processing time.
3. Check **GitHub jobs**, **GitHub repositories → Last sync**, Render memory/CPU,
   and database/disk usage. Test a representative larger PDF before announcing sync.

The scheduler shares the existing web runtime and disk. Do not add an independent
Render Cron container that writes native files without access to that disk. No
separate Redis/worker is necessary for this configuration. Horizontal scaling
requires shared object storage and an appropriate separate job runtime first.

## 11. Email and public registration remain pending ITU

Keep `FGTIDA_MAIL_ENABLED=false` until ITU provides the approved SMTP credentials
or API contract. The outbox/templates exist; an API-specific transport still needs
to be implemented against ITU's contract. Configure recipients now, but no email
is sent yet. New self-registered users cannot activate until confirmation delivery
works; existing users/admin MFA remain available.

After ITU setup, test confirmation (including expired/reused links), reset plus
recovery checks, and one WG/Incubation notification with its attachment. Verify
authorized sender `tsbfgtida@itu.int` and deliverability before enabling production
mail and public registration. Run the final publication check without the deferred
SMTP exception. Never drain stale outbox messages blindly; expired links are skipped.

## 12. Failures, rollback and future WGs

| Symptom | Check / action |
| --- | --- |
| Publisher says skipped | Review/merge `.tracker/metadata.json` PR; confirm correct linked document. |
| HTTP 503 from publisher | Confirm deployed code, ingest flag, App/JWKS connectivity and logs. |
| HTTP 409 | Inspect the returned validation message: wrong workflow/ref, stale commit, duplicate/older version, paused work item or terminal state. Do not weaken identity checks. |
| HTTP 401/403 webhook | Compare webhook secrets and configured repo IDs/installation mappings. |
| No automatic updates | Check scheduler flag/logs, job attempts/next retry, App installation access and enabled source mappings. |
| Pages fails on a tag | Restrict the Pages deploy job to `refs/heads/main`; inspect whether tracker archiving succeeded separately. |
| Version/date looks wrong | Compare archive history and source tag/commit. Do not alter historical dates to resemble the reference screenshot. |

To stop side effects, set write/ingest/schedule flags false and redeploy. This
does not undo an external repository/PR or erase an archived version. Document the
incident, preserve logs and restore only through the tested backup procedure if
needed. Prefer a forward fix after migrations over a blind code/database rollback.

For a future WG, create its group in admin, assign an unused number and topic
acronym in **Working group configurations**, install/map the App for its GitHub
organization, and configure recipients. Incubation adoption must select that WG;
a missing charter gets a draft charter work item. Review discovered repositories'
types and canonical paths before treating their imports as official documents.

Update the public contribution guide to describe X.Y versions, the required
reviewed repository binding, and the editor's content-transfer step. It must not
claim that a personal URL alone enables synchronization.

References: [GitHub App registration](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app),
[repository creation permission](https://github.blog/changelog/2026-10-13-github-app-improvements-for-least-privilege-and-management/),
[Render environment and secret files](https://render.com/docs/configure-environment-variables),
[GitHub integration reference](GITHUB-INTEGRATION.md).
