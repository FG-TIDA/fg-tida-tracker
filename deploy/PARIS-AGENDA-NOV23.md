# Paris agenda revision of 23 November 2026

The 27 rows in `ietf/fgtida/paris_agenda_2026.json` were transcribed from the
provided revised Word agenda. They retain its event text, presenters, outcomes
and clock times. Per the administrator's correction, sessions with an explicit
WG heading use `Co-Chairs WG<n>` unless explicitly overridden below. The document's extra duration annotations are not used to
override its explicit start/end times. Rooms remain ROOM XI, with dinners TBC.

ICAP is WG6 (Session 16). Following the administrator's corrections, WGA, WGB,
WG4 are Sessions 17, 18 and 19 respectively. Feedback is Session 18 as explicitly
requested (the WGB session also retains label 18). Numbered subitems follow their
parent session. WG4 retains 16:30–16:45; coffee is 16:45–17:00; feedback is
17:00–17:30; dinner is 17:30–18:15. Time cells contain clock ranges only, without
duration annotations. Presenter overrides: WG3 → WGX Co-Chairs; WG4 → WGY
Co-Chairs; WG5 and WG6 → WGZ Co-Chairs. No new working groups are created from
agenda headings.

After deploying, back up the production database, then open the Render web
service Shell. Preview the update:

```sh
python ietf/manage.py update_paris_agenda --meeting tida-2026-11-30-first-focus-group-meeting --skip-checks
```

Apply it:

```sh
python ietf/manage.py update_paris_agenda --meeting tida-2026-11-30-first-focus-group-meeting --apply --skip-checks
```

Repeat application keeps existing topic/session identifiers and attachments.
Earlier unmatched sessions remain stored off the published agenda. Check both
days on the public agenda and edit any outstanding source conflicts in admin.

## Leadership without login accounts

1. Open **Admin → People → Add** (`/admin/person/person/add/`). Enter the person's
   name and their contact email in the email inline. Leave **User** blank.
2. Save the People profile. This does not create a login account.
3. Open **Admin → Groups → Roles → Add** (`/admin/group/role/add/`). Select the
   group, role, People profile and its contact email, then save. Alternatively
   use the Roles inline on the group's edit page.
4. Chair, Co-Chair, Editor and Rapporteur are available. Assigning these roles
   does not grant a user account, login credentials or administrator access.

Migration `0045_rapporteur_role` adds the Rapporteur choice. Run normal database
migrations through the existing pre-deploy command; do not create a User merely
to list someone in group leadership.
