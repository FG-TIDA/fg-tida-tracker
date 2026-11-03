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
| Document workflow | Submitted → Draft → Under review → Approved → Published, with Withdrawn available. Editors choose the appropriate state; native state events and revision history remain authoritative. These labels do not impose IETF-specific governance approvals. |
| Markers | Green for New/Approved/Published/Announced; blue for Updated/Submitted; yellow for Under review/Rescheduled/Cancelled; red for Withdrawn/Not approved; grey for draft or unrecorded states. Every marker includes its text label. |
| Recency | New means the first recorded event was in the last 14 days. Updated means an older document received a new revision in the last 14 days. Metadata-only edits do not create an Updated marker. |
| Search | Filter by type, owning group, category, lifecycle state, author, and recent activity; sort by update time, identifier, or title. Filters persist through pagination. Category markers link to the corresponding catalog. |
| Archives | Past/upcoming dates remain independent of workflow status. A cancelled future meeting remains discoverable in upcoming results; status filters use each session's latest event. |

The existing blue header, logo, buttons, layout, time-zone controls, and Markdown
rendering are unchanged by this workflow update. RFC publication, IESG ballots,
and IETF-specific approvals are not appropriate substitutes for FG-TIDA editors.
Actual announcement delivery, registration, and institutional approval remain
external/manual processes; the tracker records their confirmed state.
