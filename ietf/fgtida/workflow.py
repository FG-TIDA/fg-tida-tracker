"""FG-TIDA presentation of native document and scheduling state."""
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from ietf.meeting.models import Session, SchedulingEvent

SESSION_STATES = {
    'apprw': ('Pending approval', 'secondary'),
    'appr': ('Approved', 'info'),
    'schedw': ('Awaiting scheduling', 'secondary'),
    'scheda': ('Awaiting announcement', 'info'),
    'sched': ('Announced', 'success'),
    'resched': ('Rescheduled', 'warning'),
    'canceled': ('Cancelled', 'warning'),
    'canceledpa': ('Cancelled before announcement', 'warning'),
    'disappr': ('Not approved', 'danger'),
    'notmeet': ('Not meeting', 'secondary'),
    'deleted': ('Deleted', 'secondary'),
}
CANCELLED = {'canceled', 'canceledpa'}
DOCUMENT_COLORS = {'submitted': 'info', 'draft': 'secondary', 'review': 'warning',
    'approved': 'success', 'published': 'success', 'withdrawn': 'danger',
    'active': 'info', 'deleted': 'secondary'}
RECENT_DAYS = 14


def session_status(session):
    if not hasattr(session, 'current_status'):
        session.current_status = Session.objects.with_current_status().values_list('current_status', flat=True).get(pk=session.pk)
    label, color = SESSION_STATES.get(session.current_status, ('Status not recorded', 'secondary'))
    return {'label': label, 'color': color}


def meeting_status(meeting):
    sessions = getattr(meeting, 'workflow_sessions', None)
    if sessions is None:
        sessions = list(meeting.session_set.with_current_status())
    statuses = {session.current_status for session in sessions}
    if statuses and statuses <= CANCELLED:
        return {'label': 'Cancelled', 'color': 'warning'}
    if statuses & CANCELLED:
        return {'label': 'Partially cancelled', 'color': 'warning'}
    if len(statuses) == 1:
        label, color = SESSION_STATES.get(next(iter(statuses)), ('Status not recorded', 'secondary'))
        return {'label': label, 'color': color}
    return {'label': 'Mixed session statuses' if statuses else 'Not scheduled', 'color': 'secondary'}


@transaction.atomic
def set_session_status(session, status, actor):
    if status not in SESSION_STATES:
        raise ValueError('Unsupported scheduling status')
    Session.objects.select_for_update().get(pk=session.pk)
    latest = session.schedulingevent_set.order_by('-time', '-pk').first()
    if latest is None or latest.status_id != status:
        SchedulingEvent.objects.create(session=session, status_id=status, by=actor)


def recent_marker(doc):
    cutoff = timezone.now() - timedelta(days=RECENT_DAYS)
    first = getattr(doc, 'first_activity', None)
    if not hasattr(doc, 'first_activity'):
        first = doc.docevent_set.order_by('time', 'pk').values_list('time', flat=True).first()
    if first and cutoff <= first <= timezone.now():
        return 'New'
    revision = getattr(doc, 'latest_revision', None)
    if not hasattr(doc, 'latest_revision'):
        revision = doc.docevent_set.filter(type='new_revision').order_by('-time', '-pk').values_list('time', flat=True).first()
    return 'Updated' if revision and cutoff <= revision <= timezone.now() else ''
