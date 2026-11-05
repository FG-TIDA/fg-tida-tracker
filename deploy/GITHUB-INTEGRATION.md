# FG-TIDA GitHub workflow deployment

This feature needs a deployment and administrator setup before live synchronization
starts. No GitHub credential is included in the repository. ITU email delivery remains
disabled until ITU supplies its authorized transport. Existing accounts and MFA devices
are preserved. New self-registered accounts require email confirmation; with email
disabled, their confirmation messages wait in the database outbox and those accounts
cannot sign in until confirmed. Do not announce public registration before ITU mail works.

## Editorial and revision rules

1. A signed-in participant submits a file, title, abstract and WG or Incubation target.
   The original file and contribution are stored in PostgreSQL, and a public native
   document record is created with status **Submitted**, revision **00**. The form
   explicitly tells participants the submission is public. Private review feedback
   and recovery answers are never copied to GitHub.
2. An editor adopts or rejects it in **Admin → Contributions**. For Incubation,
   adoption requires selecting an existing or newly created WG; a missing charter
   gets its own draft work item. Add future WGs under **Groups**, then set their
   unique number, topic acronym and GitHub organization in the configuration inline.
3. Adoption changes the document to **Draft** and queues an official repository
   from the FG-TIDA specification/charter template. The integration opens a metadata
   pull request, including the title, public author names and tracker status badge.
   An editor must review/merge it and incorporate the submitted draft. Imported
   existing work items can use **Admin → Work items → Queue repository creation /
   metadata pull request** to connect their existing repository.
4. Subsequent changes go through pull requests. Main-branch PDF builds update the
   **working copy**, preserving the archived revision. Release tags such as `v1.0`
   archive PDF, source TAR.GZ and manifest under the next native revision **01**,
   **02**, etc. An initial GitHub import is revision **00**. Tags are provenance,
   not the public revision number. Replayed identical releases are idempotent;
   replacement tags and non-increasing versions are rejected. Protect release tags
   against deletion/moving. Make the PDF build reproducible, including its timestamps.
5. Editors set **Under review**, **Published**, or **Withdrawn** in the tracker.
   Publication of a linked GitHub work item requires an archived release. Terminal
   states reject further builds. A published work item needs a new document/explicit
   editorial process for new work. GitHub issue closure does not constitute approval.

## Before deployment

Take a Render PostgreSQL backup and a persistent-disk backup. Deploy the reviewed
commit; keep the current pre-deploy migration command:

```sh
python ietf/manage.py migrate --noinput --skip-checks
```

In the Render service shell run:

```sh
python ietf/manage.py configure_fgtida_github --skip-checks
python ietf/manage.py check
```

The configuration command seeds WG0 (Use Cases and Terminology), WG1 (Continuous
and Dynamic Remote Attestation for Agentic AI), WG2 (Identity, Behaviour and
Enforcement for AI Agents), and explicit document sources. Names remain editable
and later runs preserve edited mappings. Topic names come from the public WG
profiles/charters; draft charters are not automatically approved. The WG0 `uct`
repository acronym is an editable local shorthand, not an asserted official acronym.
The old, unconfigured WG3 placeholder is marked Concluded only if it still has
its generic name and no documents, sessions, memberships, milestones, submissions
or configured recipients. Real records and custom group names prevent this cleanup.

## Create and install a GitHub App manually

Use an organization-owned App, not a personal access token. In GitHub organization
**Settings → Developer settings → GitHub Apps → New GitHub App**, use the tracker
home page as Homepage URL. No user authorization callback is required for this
server-to-server integration. Configure:

- Webhook URL: `https://tida-datatracker.org/integrations/github/webhook/`.
- Generate a random webhook secret locally (at least 32 random bytes) and retain
  it in your secret manager. Do not send it through chat or put it in GitHub files.
- Repository permissions: **Contents: read/write**, **Pull requests: read/write**,
  **Issues: read/write**, and default **Metadata: read**.
- Organization permission: **Repository creation: write**, where available.
  Do not grant Administration/Workflows write just to bypass a setup failure.
- Subscribe to **Push** and **Issues** webhook events.
- Permit installation in the central FG-TIDA organization and the WG organizations.

Install the App in **FG-TIDA** for the canonical templates and the configured
content repositories (themes, use-cases, meetings, marketing, wg1-ra-aai).
The tracker code repository does not need installation just to import documents.
Install it in the dedicated **fg-tida-wg0**, **fg-tida-wg1**, **fg-tida-wg2**
organizations; access to **all repositories in those dedicated WG organizations**
allows it to manage newly created WG repositories. If policy requires selected
repositories instead, an owner must grant access after each repository creation.
Confirm organization policies allow the App to create from the FG-TIDA templates.
Future WGs need their own admin-configured organization/installation mapping.

Generate the App's private PEM key in GitHub. In **Render → Web Service →
Environment → Secret Files**, upload it as `github-app.pem`; add a second secret
file `github-webhook-secret` containing the webhook secret. File paths are normally
`/etc/secrets/github-app.pem` and `/etc/secrets/github-webhook-secret`.
Set environment variables (these names are configuration; no secret values belong
in Git):

```text
FGTIDA_GITHUB_APP_ID=<App ID shown in GitHub>
FGTIDA_GITHUB_PRIVATE_KEY_FILE=/etc/secrets/github-app.pem
FGTIDA_GITHUB_WEBHOOK_SECRET_FILE=/etc/secrets/github-webhook-secret
FGTIDA_GITHUB_WRITE_ENABLED=false
FGTIDA_GITHUB_INGEST_ENABLED=false
FGTIDA_GITHUB_SCHEDULE_ENABLED=false
```

Redeploy. In **Admin → GitHub accounts**, enter the installation ID for each
organization (the number in the GitHub installation settings URL, not the App ID).
No passwords or tokens are entered into the database. Test **Redeliver → ping**
in the App webhook delivery history; a valid ping returns `ready`.

## Import the public catalog

Review **GitHub repositories** in admin: group, mode, paths and document type.
Only configured paths and supported files are imported. README, HOWTO, CHANGELOG,
source code and configuration files are excluded. Issues become theme/use-case
records, and explicitly linked GitHub-hosted document attachments are imported.
Existing manual records are preserved. Markdown and reStructuredText are rendered
with sanitization; repository build code is not executed by the tracker.

```sh
python ietf/manage.py sync_fgtida_github --skip-checks
# Review the read-only plan first, then apply:
python ietf/manage.py sync_fgtida_github --apply --skip-checks
# Optional future WG repository discovery within configured organizations:
python ietf/manage.py sync_fgtida_github --discover --apply --skip-checks
```

Use the App for reliable imports; anonymous GitHub API requests are rate limited.
Imports do not send group notifications for historic records. New contributions
use **Contribution recipients**: add each WG's recipient addresses and the
Incubation management addresses there. Public GitHub mailing-list names do not
automatically subscribe anyone or authorize mail delivery.

Editing an imported document's content/group/metadata in native admin pauses its
source sync. Deleting it leaves an excluded source marker, so the next sync cannot
recreate it. Resume deliberately through **GitHub sources → Sync from GitHub**;
also resume the linked **Work item** when appropriate. To restore a deleted source,
create/select the intended document and explicitly rebind that source. Archives
are immutable/read-only in admin. Admin-created standalone documents remain valid
without a GitHub repository. Do not enable external writes until the mappings have
been reviewed.

## Add publishing to the canonical GitHub build workflow

An organization owner must manually edit `.github/workflows/docs.yml` in both
canonical templates and in existing work-item repositories. The App intentionally
has no permission to write workflow files. Keep the existing PDF/HTML build steps;
add publishing **after a successful PDF build**, in the same job. Use the actual
PDF output path from that repository. Register the exact workflow path in the
repository's admin record if it differs from `.github/workflows/docs.yml`.

```yaml
on:
  push:
    branches: [main]
    tags: ['v*']
  workflow_dispatch:

# In the PDF-building job:
permissions:
  contents: read
  id-token: write
steps:
  # Keep checkout and the existing build steps here.
  # After the build, append:
  - name: Publish working copy or archived release to Datatracker
    if: github.event_name != 'pull_request'
    uses: FG-TIDA/fg-tida-tracker/.github/actions/publish-work-item@REVIEWED_COMMIT_SHA
    with:
      pdf: src/doc/_build/latex/ACTUAL_OUTPUT_NAME.pdf
      source: src/doc
      tracker-url: https://tida-datatracker.org
```

This is a placement example, not a replacement for the full build workflow.
Replace `REVIEWED_COMMIT_SHA` with the full reviewed tracker commit containing the
action; pin all third-party actions too. Never allow fork PR workflows to publish
or receive secrets. A short-lived GitHub Actions OIDC identity is verified against
the configured workflow, immutable repository ID, current branch/tag and commit.
No tracker upload token is stored in Actions. Source archives exclude build output,
environment files and credential extensions; still review source before publishing.
Protect `main` with required PR review and required successful builds; protect
`v*` tags against moving/deletion. Restrict workflow editing to trusted maintainers.

After reviewing the metadata PR and build workflow, set
`FGTIDA_GITHUB_INGEST_ENABLED=true`, redeploy, and test a main build and a first
release. Verify the tracker working copy and `00 → 01` archive behavior. Then
enable `FGTIDA_GITHUB_WRITE_ENABLED=true` when ready to process adoption/state jobs.

## Schedule and monitor

After the initial import succeeds and App permissions are tested, set
`FGTIDA_GITHUB_SCHEDULE_ENABLED=true` and redeploy. The Render supervisor starts a
small maintenance process beside Gunicorn, sharing the same `/data` disk and
environment. It processes queued jobs approximately every minute and polls the
catalog (including new numbered WG repositories) every 15 minutes. Processing time
is added to each interval. Catalog polling has a shared PostgreSQL advisory lock;
jobs have transactional row locks. Errors retain retry state and are logged without
credentials. Check service memory under a representative import before enabling;
this is intended for the existing single-instance, disk-backed deployment.

An alternative is the host's approved scheduler invoking commands inside the deployed
runtime with the same database/environment. Do not put secrets in cron arguments. A
separate Render Cron service needs its own secret configuration and database access;
it cannot access the web service's disk. Catalog imports write native document
files, so run catalog/job commands in the web runtime that owns `/data`, or first
implement shared object storage. A separate Cron container without that storage
is not a safe drop-in deployment. With scheduling disabled, these are manual
commands in Render Shell:

```sh
python ietf/manage.py process_fgtida_github --limit 20 --skip-checks
python ietf/manage.py sync_fgtida_github --apply --skip-checks
```

Recommended cadence: process jobs every minute, poll public catalog every 15–30
minutes as a webhook fallback. Jobs retain retry/backoff/error state in PostgreSQL;
disabled external writes remain queued. Monitor failed/exhausted jobs in admin.
The built-in scheduler is opt-in, not enabled by default. A separate worker/Redis
service is not required for these durable jobs. Keep regular PostgreSQL and `/data`
backups. Horizontal scaling later requires shared object storage, a separate
supervised job service and a review of concurrent build/resource limits.

## Email rollout after ITU approval

The database outbox and professional confirmation, reset and contribution messages
are implemented. Sender is **FG-TIDA Secretariat <tsbfgtida@itu.int>**. Messages
begin `Dear <given name>,` (or a suitable fallback), end `Regards,`, with no
signature. Confirmation expires after 24 hours; resets after one hour. All three
recovery answers and optional/required MFA policies remain in force as applicable.
Existing accounts are not forced to reconfirm. User confirmation is a POST step,
so email security scanners following a link cannot activate an account.

ITU must provide the authorized SMTP configuration or API contract. For an API,
implement a Django email backend/transport adapter against that contract; do not
invent credentials/endpoints. Keep `FGTIDA_MAIL_ENABLED=false` until real delivery
and sender authorization (including SPF/DKIM/DMARC with ITU) are tested. Then use
the existing outbox delivery command/scheduler; expired confirmation/reset messages
are skipped, and users can request fresh links. Never bulk-send stale imports.

References: [FG-TIDA workflow](https://fg-tida.github.io/.github/#new),
[GitHub Actions OIDC](https://docs.github.com/en/actions/reference/security/oidc),
[GitHub App permissions](https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps),
[Repository creation permission](https://github.blog/changelog/2026-10-13-github-app-improvements-for-least-privilege-and-management/),
[IETF revision guidance](https://ietf.github.io/id-guidelines/).
