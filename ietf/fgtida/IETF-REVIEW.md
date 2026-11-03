# IETF Datatracker review and FG-TIDA adaptation

Reviewed 23 October 2026. This review covers the live public pages and the upstream
Datatracker code already in this checkout. The browser automation runtime failed
to initialize, so this was a page-content and source-code inspection, not a
screenshot-based visual audit or an inspection of IETF's private administration.

## Reference surfaces

* [Home and dropdown navigation](https://datatracker.ietf.org/): groups by parent
  and lifecycle, document search criteria, meeting materials, upcoming/past
  meetings, important dates, account tools and IETF governance sections.
* [TLS group About](https://datatracker.ietf.org/group/tls/about/): charter,
  personnel, mailing-list resources, milestones and linked documents.
* [TLS documents](https://datatracker.ietf.org/group/tls/documents/): grouped
  lifecycle status, identifiers, dates and revisions.
* [TLS meetings](https://datatracker.ietf.org/group/tls/meetings/): group-scoped
  meeting history, materials, remote tools, recordings and calendar exports.
* [TLS photos](https://datatracker.ietf.org/group/tls/photos/): About, Documents,
  Meetings, History, Photos, Email expansions and List archive subnavigation.
  The photos are based on assigned roles; they are not proof of a complete
  membership register. FG-TIDA explicitly includes the native Member role.
* [Group history](https://datatracker.ietf.org/group/tls/history/): dated changes
  with actors, distinct from document revision history.
* [Draft detail](https://datatracker.ietf.org/doc/draft-ietf-tls-tlsflags/) and
  [history](https://datatracker.ietf.org/doc/draft-ietf-tls-tlsflags/history/):
  versions, formats, authors, affiliations, state, relationships and comparisons.
* [RFC detail](https://datatracker.ietf.org/doc/rfc8446/): published-document
  metadata, format links and relationships.
* [Meeting materials](https://datatracker.ietf.org/meeting/materials),
  [TLS session](https://datatracker.ietf.org/meeting/124/session/tls), and
  [important dates](https://datatracker.ietf.org/meeting/important-dates/):
  separate session agendas, minutes, slides, recordings, deadlines and calendars.

The local reference code includes `group/utils.py` (conditional group tabs),
`group/views.py` (role-based photos), `templates/doc/document_format_buttons.html`,
`templates/meeting/session_buttons_include.html`, and native Person, Role,
Document, DocHistory, GroupEvent, Meeting, Session and SessionPresentation models.

## Implemented FG-TIDA behavior

| Area | FG-TIDA implementation |
| --- | --- |
| Catalog curation | 25 imported support records excluded using the local manifest; original records, files and history retained. No recurring GitHub ingestion. |
| Templates | Two canonical records: Specification template and Working group charter template. Package links are external references; uploaded revisions remain admin-managed. |
| Group tabs | About, Documents, Meetings, Milestones, Members, Photos, History, plus List archive when configured. |
| Members | Native Person + Role records; admin adds Member/Chair/Secretariat, biographies and photos. Search and pagination; photo placeholder when no photo was supplied. |
| Group history | Native history snapshots and events for group edits and membership changes through the customized admin. |
| Document search | Names, titles and abstracts; author or affiliation, group, type, category and state filters; pagination. |
| Formats | Existing local PDF, TXT, Markdown, XML, DOCX and PPTX files get appropriate Bootstrap icons and download links. Extracted text has its own download. No fictional conversion links. |
| Recordings | Native Recording documents linked to sessions, with validated external HTTP(S) media URLs and camera icons. |
| Revisions | Native events/snapshots, original-version downloads, extracted-text comparison, bounded comparison size. |
| Relationships | Native related-document links displayed in both directions, limited to catalogued FG-TIDA records. |
| Meetings | Group/year/text filtering, upcoming and past views, paginated sessions and categorized materials. |
| Agenda | Native official schedule, sessions, time slots and rooms; meeting timezone or UTC; remote instructions. |
| Deadlines | Native ImportantDate records managed in admin and displayed per meeting. |
| Calendars | Group, meeting and whole-tracker .ics exports; scheduled session times and all-day unscheduled meetings. |
| Navigation | Dropdown subheaders distinguish meeting archives/materials and group lifecycle/work programme. Existing Sign in retained; no Account header. |

## Data still needed

An empty member page means that the roster has not been entered. No people,
photos, approvals, recording URLs or future sessions were fabricated. Editors
must provide and maintain these records in admin. Template package entries do
not imply that every package file is a standards document or downloadable PDF.

## Deliberate boundaries and remaining gaps

This is an adaptation of the real Datatracker code and data model, not a claim
that every IETF feature has been reproduced or certified against a universal
Datatracker standard.

* IETF-specific IESG ballots, AD roles, RFC streams, RFC publication/errata,
  NomCom, IANA and BOF processes are outside the configured FG-TIDA navigation.
  Dependencies remain in the upstream source/schema where required.
* Email-expansion lists are not published as a new FG-TIDA feature; member names
  and assigned roles are visible, with mailing-list links when configured.
* Automatic multi-format rendering (e.g. DOCX to PDF), arbitrary full-file search,
  external-site search, and unuploaded files are not provided by metadata search.
* Subscription notifications, production mail delivery, self-service registration,
  review assignments and an FG-TIDA-specific formal approval state machine need
  separate workflow configuration. Current approvals are explicit administrator
  state changes recorded in native history.
* Meeting participation rosters, bluesheets, chat logs, polls, live conferencing,
  room maps and automated proceedings generation remain further work if FG-TIDA
  requires them. Recording links do not provide a media-hosting service.
* Calendar exports can be imported/subscribed to by calendar clients; they do not
  send invitations or reminders. Recurrence is entered as additional native
  meetings, preserving independently editable agendas and materials.
* Catalog exclusion is classification, not secrecy. Production API/storage access,
  backup/restore, concurrent-write behavior, load tests and deployment monitoring
  remain separate operational checks. Pagination alone does not prove capacity.

## Validation

Focused integration tests cover admin uploads and approval history, catalog
exclusion and idempotent cleanup, members/profile/photo serving, recording
materials, text downloads and differences, calendar times, and group scoping.
Photo storage and serving use a temporary local image fixture; external media
services are not tested. Pagination checks cross the 25-record boundary for
documents and future meetings. Live localhost checks complement the database tests.
