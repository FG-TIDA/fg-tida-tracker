# Paris agenda DCO revision of 23 November 2026

The agenda follows the supplied DCO Word document, with later adjustments below: sessions 1–18 and WG3
Discovery, WG4 Governance/Cross-Border/Interoperable Credentials, WG5 ICAP, WG6
Athena, WG7 Protected Groups/Child Online Protection and WG8 Control Plane.
Presenter labels follow that document. The Opening of Day 2 presenter references
were corrected from 10.1/10.2 to 9.1/9.2 to match Session 9.

On 24 November, WG0 was renamed to Use Cases, Taxonomy, and Terminology (UCT),
WG1 was confirmed as Continuous and Dynamic Remote Attestation for Agentic AI (CDRA), and WG5 was renamed to
Implementation Conditions and Adoption Pathways for Trusted Agentic AI (ICAP).
The agenda data includes these approved names. Migration 0046 updates existing
group names, topic acronyms and live agenda text while preserving group/session
IDs, repository mappings and archived documents. Normal deployment migrations
apply those naming updates; fresh installations use the updated defaults.

Existing room assignments are retained; new assignments default to ROOM XI,
with dinners TBC. Session/material identities follow topics when numbering changes.
Actual working groups are not created or renamed by the agenda update.

On 26 November, the final 1 December coffee break (16:45–17:00) was removed.
Session 18 now runs 16:45–17:15; the networking and Trustech dinner party runs
17:30–22:00, retaining the requested 15-minute gap. There are now 26 rows.
Migration `0050_adjust_paris_closing_agenda` updates only these closing entries
in the official schedule during deployment, preserving session/material links,
rooms, earlier breaks and alternative/historical schedules. It also works if an
administrator already deleted the final coffee break.

## Production after deployment

Migration `0047_remove_wg5_from_working_groups` removes WG5 from the working
group roster and submission choices. Its administrative record and linked data
are retained, and its agenda session is unchanged. The normal pre-deploy
`migrate` command applies this change; setup commands no longer seed WG5.

Back up the database and files. In Render → web service → Shell:

```sh
python ietf/manage.py update_paris_agenda --meeting tida-2026-11-30-first-focus-group-meeting --skip-checks
python ietf/manage.py update_paris_agenda --meeting tida-2026-11-30-first-focus-group-meeting --apply --skip-checks
```

For the download, open **Admin → Documents → Documents**, find
`agenda-fg-tida-first-meeting-2026-11-30`, and upload the supplied
`FG-TIDA_1st_meeting_agenda_revised Nov 23 DCO.docx` on the existing record.
Save rather than creating a duplicate document. Previous versions remain archived.

Alternatively transfer that DOCX privately to the Render service and update both
the schedule and the downloadable agenda together:

```sh
python ietf/manage.py update_paris_agenda --meeting tida-2026-11-30-first-focus-group-meeting --agenda-file /tmp/paris-agenda-nov23-dco.docx --agenda-document agenda-fg-tida-first-meeting-2026-11-30 --apply --skip-checks
```

The command validates the file and keeps the document identifier and meeting links.
Repeating with identical current file bytes creates no extra version. The Word
file is not stored in the source repository. Verify both days and the Materials
download after updating.
