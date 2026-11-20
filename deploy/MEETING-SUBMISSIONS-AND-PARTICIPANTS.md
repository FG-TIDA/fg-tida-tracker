# Meeting-linked submissions and participants

The Paris deadline is **23 November 2026, 23:59 UTC**. Submission windows are
evaluated on each request and checked again after file processing, before saving.
Closing does not depend on a scheduled job. New website NWIs require an open
meeting selection. Archived revisions keep their existing meeting association.

## Local validation before deployment

Run these regression tests against the local development container before deploying:

```powershell
docker exec fg-tida-datatracker-app-1 python ietf/manage.py test ietf.fgtida.test_credit_accuracy ietf.fgtida.test_meeting_submissions ietf.fgtida.test_source_submissions.SubmissionTests ietf.fgtida.test_admin_contributions ietf.fgtida.test_document_lists --testrunner django.test.runner.DiscoverRunner --keepdb --noinput
```

No tests should be run against the production database.

## Apply to Render

Take a database backup, commit/push the reviewed code and deploy the latest
commit. Keep the existing pre-deploy migration command. Migrations 0042–0043
add the meeting controls and participant table, associate unassigned existing
contributions received before the Paris deadline, and correct the existing
Paris session titles, presenter placeholders and official schedule locations.
They do not reset already assigned meeting links or copy participant data.

Then run in Render Shell:

```sh
python ietf/manage.py configure_fgtida --skip-checks
python ietf/manage.py scan_fgtida_files --limit 1000 --skip-checks
python ietf/manage.py refresh_fgtida_collections --apply --skip-checks
```

The configuration command updates editor permissions, without granting account
roles. The scan command applies the separately prepared document publication
policy. The refresh fetches the authoritative `fg-tida-wg0/use-cases` and
`FG-TIDA/themes` repositories, repairs use-case ownership/numbering and fills
missing metadata only where explicitly declared in source content. It preserves
administrator metadata overrides and does not write GitHub or send submission
emails. Review the summary for unstated author/affiliation evidence; correct
those in the source or Public metadata rather than guessing a person's identity.

For a read-only refresh plan, omit `--apply`. To repair already imported local
content without fetching GitHub, add `--no-sync`. Local database changes are not
copied to Render by a GitHub push; run the production commands there.

## Control deadlines in admin

1. Open **Meetings → Paris meeting** and find **NWI submission deadline**.
   Alternatively open **FGTIDA → Meeting submission windows**.
2. Verify **Accepting submissions** is enabled and **Submission deadline (UTC)**
   is `2026-11-23 23:59 UTC`.
3. To close immediately, disable **Accepting submissions**.
4. To reopen, enable it and enter the final date/time under **Reopen / extend
   until (UTC)**. This value overrides the original deadline while set. Clear
   it to return to the original deadline. Dates in these fields are explicitly
   UTC regardless of the admin browser timezone. Submission windows never
   extend beyond the end of a meeting.

A meeting without a configured deadline closes at the start of its first day,
in the meeting's timezone. New GitHub-imported use cases are associated with the
open meeting only when exactly one is available. If several are open, assign
the intended meeting in **Admin → Contributions**; no meeting is guessed.
Website contributors always make their own selection. The document search's
Meeting criterion includes NWI assignments, input/output listings and native
session materials without adding a meeting column to the general results table.

## Paris agenda

The current agenda retains its session records and materials. Missing WG titles
are filled from the configured WG names. Placeholder presenters WGX/WGY/WGZ
become WG3/WG4/WG5 Co-Chairs according to their session. Official schedule rows
use **ROOM XI**, with dinners in **TBC**. These targeted corrections apply in
migration 0043. The updated 25-row import also follows these rules; run it only
when intentionally replacing the agenda text from the reviewed source:

```sh
python ietf/manage.py update_paris_agenda --apply --skip-checks
```

## Participant lists

Each meeting now has **Participants** in its navigation, for example:
`/meeting/tida-2026-11-30-first-focus-group-meeting/participants/`.

The table uses the IETF example's columns: Family Name, Given Name, Organization,
Country/Territory and Reg Type. Remote and Onsite filters are available. All lists
start empty. No registrations, user accounts or IETF participant data are copied.

Later open **Admin → Meeting participants → Add**, choose the meeting, enter
the supplied names/affiliation/country and select Remote or Onsite. An account is
not required. Check **Published** only when the data has been authorized for
public listing. Unpublished records remain invisible on the public page.

## Metadata corrections and missing upload investigations

Migration 0044 records automatic metadata separately from deliberate editor
choices. In **Documents → document → Public metadata**, enter one author per
line. Saving a changed field makes it authoritative, even if deliberately
cleared. Changing the assigned author profiles instead makes those profiles
authoritative unless Public metadata is also edited in the same save.
GitHub synchronization does not overwrite these choices.

After deploying and migrating, repair old malformed imported credits with:

```sh
python ietf/manage.py refresh_fgtida_collections --no-sync --apply --skip-checks
```

This uses explicit source declarations, ignores Markdown/RST separator rows,
and splits labelled Name/Organization/Country/Email blocks into their fields.
Unstated author identities remain unset. It preserves deliberate editor choices.
The client label is **Author(s)**. Meeting links are not shown on contribution
detail pages; the meeting association remains available to search and admin.

For uploads from 18–20 November, including files whose status changed after
rescanning, run this **read-only** report in Render Shell:

```sh
python ietf/manage.py audit_fgtida_uploads --since 2026-11-18 --until 2026-11-21 --match-originals --skip-checks
```

The end date is exclusive and all report dates are UTC. It lists scan records
created, checked or audited within that range across **all statuses**, retained
private originals, saved contribution associations and recent saved contributions.
`--match-originals` also hashes the stored contribution originals/source packages
to match older records that predate checksum metadata. It reads those bytes once
without parsing, executing, publishing or sending them anywhere.
It does not open or release a file, expose contributor account details, create
a contribution or send email. Filenames and document identifiers are included;
keep the report in the authorized administrator context.

In **Admin → File scans**, clear existing filters, use the created/checked date
filters and inspect **Original file in private quarantine** and **Linked
contributions**. Rescanning changes a verdict; it does not delete the scan row.
If a file is clean and linked, check its document page. If it is unlinked, confirm
whether the form failed before a contribution was saved; a fresh submission may
be required. If no scan row exists, check server logs for that submission rather
than assuming antivirus removed it. File validation can fail before scanning.

Check `quarantine_directory` in the report against the Render persistent disk
mount. `temporary_quarantine_warning: true` means the directory is under `/tmp`
and can disappear on deploy. The documented production configuration uses
`FGTIDA_DATA_DIR=/data` and `/data/quarantine`, outside public media. Do not change
the directory until any retained files in the old location have been accounted
for. Private quarantine files expire after seven days; verdict/audit records stay.
Uploads from 18–20 November are not seven days old on 20 November.

Previously, passing a scan immediately deleted the quarantine copy, even if a
later submission validation failed. The revised cleanup retains unlinked clean
originals privately for the bounded seven-day recovery period. Duplicate copies
are removed only after commit and verification of the saved archive's SHA-256;
the scan history records that cleanup. This cannot recover copies already
deleted by the old code: check existing contribution archives/backups first,
then request the original again if none survives. The 250 MB private queue limit
and normal expiry remain enforced.
