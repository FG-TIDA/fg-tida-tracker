# FG-TIDA workflow alignment

Reference: https://datatracker.ietf.org/meeting/upcoming and
https://github.com/ietf-tools/datatracker . The upstream implementation uses
`SessionQuerySet.with_current_status`, `SchedulingEvent`, native document states,
revision events, and recency indicators. FG-TIDA uses those same database models.
No parallel catalog, workflow database, or design theme was introduced.

| Area | FG-TIDA behavior |
| --- | --- |
| Session workflow | Editors record pending approval, approved, awaiting scheduling, awaiting announcement, announced, rescheduled, or cancelled in the session admin. |
| Announcements | The native `sched` state is displayed as Announced; an editor must confirm that publication has occurred. Selecting a state does not send email. |
| History | Every state change records its time and editor in native scheduling events. Saving the same state again does not duplicate history. History is readable in the admin and on the agenda. |
| Meeting summaries | A meeting is Cancelled only when all of its sessions are cancelled. Mixed cancellation is labelled Partially cancelled. Imported sessions with no recorded status are not presumed announced. |
| Cancellation | The meeting admin action cancels all existing sessions while retaining their times, records, and materials. Cancelled agenda sessions display a notice instead of participation instructions. |
| Calendars | Existing event identifiers are retained. Cancelled sessions export `STATUS:CANCELLED`; announced sessions export `CONFIRMED`; other sessions export `TENTATIVE`. |
| Document workflow | Submitted → Draft → Under review → Published, with rejection/withdrawal paths. Review may return a draft for changes. Legacy Approved records remain editable through explicit transitions. Editors choose the appropriate state; native state events and revision history remain authoritative. These labels do not impose IETF-specific governance approvals. |
| Markers | Green for New/Approved/Published/Announced; blue for Updated/Submitted; yellow for Under review/Rescheduled/Cancelled; red for Withdrawn/Not approved; grey for draft or unrecorded states. Every marker includes its text label. |
| Recency | New means the first recorded event was in the last 14 days. Updated means an older document received a new revision in the last 14 days. Metadata-only edits do not create an Updated marker. |
| Search | Filter by type, owning group, category, lifecycle state, author, and recent activity; sort by update time, identifier, or title. Filters persist through pagination. Category markers link to the corresponding catalog. |
| Archives | Past/upcoming dates remain independent of workflow status. A cancelled future meeting remains discoverable in upcoming results; status filters use each session's latest event. |

The existing blue header, logo, buttons, layout, time-zone controls, and Markdown
rendering are unchanged by this workflow update. RFC publication, IESG ballots,
and IETF-specific approvals are not appropriate substitutes for FG-TIDA editors.
Actual announcement delivery, registration, and institutional approval remain
external/manual processes; the tracker records their confirmed state.

## Optional GitHub work-item integration

See [deployment and administrator instructions](../../deploy/GITHUB-INTEGRATION.md).
Submission immediately creates a public Submitted document and version 0.0; review
feedback remains private. Adoption queues an official WG repository and a metadata
pull request. Incubation adoption requires a WG and a charter. Main-branch builds
update the working PDF without incrementing the archived revision. Release tags
archive PDF/source/manifest as 0.1, 0.2, etc., retaining the GitHub tag as provenance.
The revision selector uses the upstream pagination classes and selected-version
color. Numbered WG topics and sync policies are administrator-managed.

Only explicitly configured public source paths and GitHub issue attachments are
documents. Support files are excluded. Manual content edits pause their source;
deletions retain excluded markers and are not recreated by the next sync. Existing
account access/MFA is preserved. New accounts require confirmation email, with
professional reset and contribution notifications through the durable outbox.
