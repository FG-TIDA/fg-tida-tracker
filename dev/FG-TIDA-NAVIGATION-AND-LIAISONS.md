# WG navigation and liaison statements

- Groups replaces Work; each configured WG includes its number and topic acronym.
- No working group yet is the public label for the existing incubation route. Stable URLs and notification routing remain intact.
- Deliverables replaces the Milestones tab. Each WG has its own Charter tab. WG0 has Use cases.
- The global charter, deliverable and use-case menu shortcuts are removed. Search remains available for all records.
- Liaison statements inputs/outputs replace the input/output list headings.
- Meetings has Agenda, Floor plan, Registration and Important dates, then separate Upcoming and Past sections. Materials, minutes and recordings are accessed inside each meeting.

## Liaison submission policy

Preserve the received/sent DOCX, PDF or ZIP unchanged as the authoritative original. Keep direction, sending organization, date and related meeting in the existing editorial fields. A PDF reading copy can be attached to the same revision through the existing alternative-format editor. A ZIP may contain DOCX/PDF statements and text/image annexes; executable files and nested archives are rejected. Extraction supplies reading text, not a replacement document. The automatic Word-to-RST conversion is only for new technical contributions, not liaison correspondence.

## Local data audit (13 November 2026)

Before migration: 119 public documents; 124 unique referenced current/historical archive files; zero missing files. 34 use cases were assigned to the FG-TIDA parent group. No documents were assigned to the input/output lists, explaining their empty results.

Migration 0020 moves use-case ownership to WG0 and renames the incubation group. After migration: the same 119 public records and 124 files, zero missing archives, with 34 use cases now in WG0. It does not delete or replace uploads, revisions, accounts, outbox records or contribution targets. Historic submission routing is retained.

## Validation

The combined 110-test run passed 108 tests and identified two outdated test assumptions: a migration test used live rather than historical models, and a navigation assertion expected the removed global Deliverables link. After correcting those tests, all four focused navigation, migration preservation, liaison ZIP and recency tests passed. Django system checks and patch whitespace checks passed. Local home, WG0 use cases, WG1 charter/deliverables and floor-plan routes returned HTTP 200.

## Production deployment

Code pushes do not copy or replace the production database or uploaded files. Production must retain its existing PostgreSQL database and persistent /data disk. Do not run a fresh import or the local clean-database setup to deploy this code. Apply the reviewed migrations using the existing deployment process. Use `audit_fgtida_content` after deployment and maintain database/disk backups as described in deploy/BACKUP-RESTORE.md. This local audit does not certify the live production filesystem.
