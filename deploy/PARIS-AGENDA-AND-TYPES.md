# Paris agenda and document types

After deploying this update, the normal pre-deploy migration converts existing
document categories into public types (Theme, Use case, Template, Deliverable,
Resource). WG0 use cases become Contribution. Existing explicit public types
are retained except for this requested WG0 correction and singular-name merges.
Category selectors and metadata rows are removed from the website and admin.
Legacy internal tags remain for synchronization and existing collection links;
they are not a second editable classification system. Training categories and
meeting types are unaffected.

To update the Paris agenda, first take a database backup, then in Render Shell:

```sh
python ietf/manage.py update_paris_agenda --skip-checks
python ietf/manage.py update_paris_agenda --apply --skip-checks
```

If the first command cannot select exactly one meeting dated 30 November 2026,
add `--meeting EXACT_MEETING_NUMBER` to both commands. The importer requires a
two-day meeting beginning on that date. The first command previews without writes.

The 25 rows are transcribed from the supplied FG-TIDA_1st_meeting.docx. Session
number gaps, repeated item numbers and provisional WG/presenter names are
preserved from the attachment. The final dinner has a start time but no stated
end, and is displayed as open-ended. No new working groups are created by importing
agenda session names.

The apply command reuses matching sessions, retains their materials and records,
updates the official schedule and keeps unmatched earlier sessions off the agenda
instead of deleting them. Repeating it does not duplicate rows. Existing scheduling
states are preserved; newly created rows are marked Approved. Edit any text,
presenter or outcome later under Admin → Sessions. Manual edits can be replaced
by deliberately running this import again.

Edit document types under Admin → Public document types. On each document, select
its public type under Public metadata. The workflow type remains an internal
storage/lifecycle setting. Source synchronization supplies an initial public type
but retains administrator overrides.
