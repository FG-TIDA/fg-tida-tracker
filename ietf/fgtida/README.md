# FG-TIDA configuration of IETF Datatracker

This app has no database models. Documents, revisions, states, authors, groups,
roles, milestones, meetings, sessions, schedules and materials use the upstream
Datatracker models and PostgreSQL tables. It personalizes the public navigation,
search pages and native Django admin; it is not a separate GitHub catalog.

## Administration

Open `/admin/` with a staff account. Existing superusers already have access.
For delegated editors, a superuser can grant staff access and membership in
`FG-TIDA Administrators`. No accounts are granted access automatically.

* Documents → Add: choose the identifier, title, group, type, category tags and
  state; upload PDF, DOCX, PPTX, TXT, Markdown or XML (10 MB maximum). Enter a
  history note. Authors and related documents use the native inline forms.
* Edit the same document to upload its next revision. Previous files and native
  history events remain available. Administrators set review/approval states.
* Meetings → Add: enter the meeting identifier, date, duration and time zone.
  An official native schedule is created automatically. Add sessions and time
  slots, then edit a session to assign its schedule and time slot.
  Important dates offers FG-TIDA contribution, agenda, registration and minutes
  deadlines. Superusers can add deadline labels under Names → Important date names
  with a `tida-` slug and a default offset of 0; each meeting retains its own date.
* Attach agenda/minutes/slides documents through the document's meeting-session
  field. Agendas and minutes require a session. Documents appear in meeting
  materials through native SessionPresentation relationships.
* Groups → Add: choose the FG-TIDA parent. Add roles, charter and milestones
  using the native group administration.
* People → Add/Edit: maintain names, biographies and photos. Group roles → Add:
  assign an existing person as Member, Chair or Secretariat and select that
  person's email. Group Members and Photos pages list these recorded roles.
  The tracker does not infer membership from GitHub activity or document authorship.
* Recordings: create a document with type Recording, an HTTP(S) external URL,
  and the meeting session. Media stays on the hosting service; no placeholder
  video or PDF download is generated.
* Templates: use the native Template type. The initial catalog has Specification
  template and Working group charter template. Administrators can upload files,
  replace external package links, and create additional templates later.
* Show in public catalog controls inclusion in the FG-TIDA browsing/search views.
  Unchecked records remain recoverable in admin. This is not a confidential-file
  access control system: upstream APIs and storage policies are separate.

Run `python ietf/manage.py configure_fgtida` once for a new installation. It is
idempotent and creates native FG-TIDA types, states, tags and the permissions
group. It does not fetch GitHub or grant users permissions.

## Operation and scope

Search covers stored names, titles and abstracts, with author/affiliation, group, type,
category and state filters. It does not claim to search arbitrary external
documents or full file contents. Supported uploads expose extracted text on
their document page. Search, meeting lists and event history are paginated.
Future meetings and additional child groups require data entry, not code changes.
Members and session lists are paginated. Calendars use stable record identifiers
and native date/time values; unscheduled meetings export as all-day events.
Meeting pages include timezone selection, categorized materials and important
dates. Revision comparisons use extracted text, limited to 100,000 characters
and 3,000 lines per revision; original files remain downloadable.

The one-time `curate_fgtida_catalog --archive PATH --apply` correction uses the
preserved import manifest to exclude housekeeping files and register the two
template packages. Omit `--apply` for a preview. It is idempotent and never fetches
remote files. Future catalog decisions are explicit admin data, not filename rules.

See [the IETF comparison](IETF-REVIEW.md) for sources, implemented features and gaps.

Native IETF applications and migration history remain installed where upstream
dependencies require them. IETF-specific governance is omitted from the FG-TIDA
navigation/admin dashboard; this is a customized upstream installation, not a
claim of complete parity with every service at datatracker.ietf.org.

The former `ietf.tida` catalog is retired after a one-time local migration.
Its archive and source code are kept under `dev/retired-tida-catalog` for recovery;
its database tables are retained as inactive backup. There is no scheduled
GitHub synchronization or GitHub-controlled publication workflow.

Back up PostgreSQL and the native document/material artifact volumes together.
This localhost development installation still needs production deployment
configuration, backups, HTTPS and delivery-service verification before public use.

Focused integration checks:

## Interface alignment with upstream Datatracker

The shared `base.html` now follows the repository's upstream template: native
Inter typography and `ietf.css`, compact responsive navigation, neutral header,
automatic section navigation, standard content width, Bootstrap tables/tabs,
theme script, and a compact footer. FG-TIDA changes are limited to the logo,
navigation destinations, search endpoint, and organization/contact links.

`fgtida/home.html` follows the upstream front page's centered introduction and
document-search layout. Its search form is also used on document results, with
expandable FG-TIDA criteria. The native appearance controls are exposed in
Preferences. The previous custom blue global theme and homepage panels are not
used by the active homepage. Admin-managed native models, uploads, histories,
membership, meeting schedules, and account routes remain authoritative.

Reference files inspected: upstream `ietf/templates/base.html`,
`ietf/templates/doc/frontpage.html`, `ietf/templates/base/menu.html`,
`ietf/templates/base/menu_user.html`, and `ietf/static/js/theme.js`.
IETF-specific RFC/IESG/IRTF governance links are intentionally excluded from the
FG-TIDA menu. This customization does not claim parity with every IETF service.

The User menu exposes the native account creation, password reset, and account
profile workflows. Administrators with auth permissions also see user management.
Account email templates identify the FG-TIDA Secretariat; SMTP delivery must be
configured for the deployment. No accounts or outbound messages are created by
the navigation changes.

Agendas always open in Meeting time unless an explicit URL selection is made.
The old agenda preference cookie is ignored. The Local option
uses the browser's IANA time zone, which the server validates before grouping
sessions by local date (including DST). Preferences retains appearance controls.
Descriptions, notes, abstracts, and Markdown previews use the
upstream sanitized Markdown renderer; downloaded source files remain unchanged.
Whitespace-only lines containing non-breaking spaces are normalized for display,
so imported Markdown tables retain their block boundaries. Header clearance is
measured on resize, with a CSS fallback and matching anchor-scroll clearance.

The Meetings menu links registration to the official ITU FG-TIDA page, where
participants select the registration link for their meeting. Important dates
are explicitly published through Administration → Meetings → Important dates;
select a meeting, a FG-TIDA date category, and its date. Only meetings with
these entries appear on the central Important dates page. Superusers can add
categories under Important date names using a `tida-` slug.

The agenda uses the meeting's official native schedule. Create sessions and
time slots, then assign sessions to that schedule in the session editor.
Published schedules show day navigation, start/end times, rooms, group/search
filters, meeting/UTC time zones, participation instructions, and material links.
All scheduled sessions are included, including meetings with more than 25
sessions. An empty schedule displays an unpublished notice; no sessions are
generated automatically. Secretariat contact is tsbfgtida@itu.int.

```
python ietf/manage.py test ietf.fgtida.tests --settings=settings_test --testrunner=django.test.runner.DiscoverRunner --keepdb
```
