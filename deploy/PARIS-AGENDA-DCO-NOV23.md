# Paris agenda DCO revision of 23 November 2026

The 27 agenda rows follow the supplied DCO Word document: sessions 1–18 and WG3
Discovery, WG4 Governance/Cross-Border/Interoperable Credentials, WG5 ICAP, WG6
Athena, WG7 Protected Groups/Child Online Protection and WG8 Control Plane.
Presenter labels follow that document. The Opening of Day 2 presenter references
were corrected from 10.1/10.2 to 9.1/9.2 to match Session 9.

Existing room assignments are retained; new assignments default to ROOM XI,
with dinners TBC. Session/material identities follow topics when numbering changes.
Actual working groups are not created or renamed by the agenda update.

## Production after deployment

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
