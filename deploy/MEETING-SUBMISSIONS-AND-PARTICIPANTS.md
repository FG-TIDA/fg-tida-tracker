# Meeting-linked submissions and participants

The Paris deadline is **23 November 2026, 23:59 UTC**. Submission windows are
evaluated on each request and checked again after file processing, before saving.
Closing does not depend on a scheduled job. New website NWIs require an open
meeting selection. Archived revisions keep their existing meeting association.

## Local validation before deployment

The new database regression run was declined by the execution permission check.
Run these tests against the local development container before deploying:

```powershell
docker exec fg-tida-datatracker-app-1 python ietf/manage.py test ietf.fgtida.test_meeting_submissions ietf.fgtida.test_source_submissions.SubmissionTests ietf.fgtida.test_admin_contributions ietf.fgtida.test_document_lists --testrunner django.test.runner.DiscoverRunner --keepdb --noinput
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
