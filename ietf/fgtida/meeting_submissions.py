"""Meeting submission windows are evaluated at submission time, without cron."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.utils import timezone
from . import core


def meeting_bounds(meeting):
    zone = ZoneInfo(meeting.time_zone or 'UTC')
    start = datetime.combine(meeting.date, time.min, zone)
    end = datetime.combine(meeting.date + timedelta(days=max(meeting.days, 1)), time.min, zone)
    return start, end


def submission_deadline(meeting, window=None):
    start, end = meeting_bounds(meeting)
    window = window or getattr(meeting, 'submission_window', None)
    deadline = window.deadline if window and window.deadline else start
    if window and window.reopen_until:
        deadline = window.reopen_until
    return min(deadline, end)


def submissions_open(meeting, now=None, window=None):
    if not meeting.date:
        return False
    now = now or timezone.now()
    window = window or getattr(meeting, 'submission_window', None)
    return meeting.type_id == 'tida' and (not window or window.accepting_submissions) and now < submission_deadline(meeting, window)


def open_meetings():
    records = core.meetings().select_related('submission_window').order_by('date', 'pk')
    identifiers = [meeting.pk for meeting in records if submissions_open(meeting)]
    return core.meetings().filter(pk__in=identifiers).select_related('submission_window').order_by('date', 'pk')


def lock_open_meeting(meeting):
    from .models import MeetingSubmissionWindow
    # Serialize final validation with administrators editing the deadline.
    # PostgreSQL's no-key lock permits unrelated contribution FK inserts,
    # avoiding a cycle with their per-group numbering locks.
    meeting = core.meetings().select_for_update(no_key=True).get(pk=meeting.pk)
    window, _ = MeetingSubmissionWindow.objects.select_for_update().get_or_create(meeting=meeting)
    if not submissions_open(meeting, window=window):
        raise ValidationError('Submissions for this meeting are closed. Choose another open meeting or contact the secretariat.')
    return meeting


def unique_open_meeting():
    records = list(open_meetings()[:2])
    return records[0] if len(records) == 1 else None
