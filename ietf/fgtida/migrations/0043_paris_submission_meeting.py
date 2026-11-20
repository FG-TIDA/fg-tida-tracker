from datetime import datetime, timezone
import re
from django.db import migrations


def update_paris(apps, schema_editor):
    alias = schema_editor.connection.alias
    Meeting = apps.get_model('meeting', 'Meeting')
    meetings = Meeting.objects.using(alias).filter(type_id='tida', date='2026-11-30', days=2)
    meeting = meetings.filter(number='tida-2026-11-30-first-focus-group-meeting').first()
    if not meeting:
        if meetings.count() == 1:
            meeting = meetings.get()
        elif meetings.filter(city__icontains='Paris').count() == 1:
            meeting = meetings.get(city__icontains='Paris')
        else:
            return  # No guessing when the meeting is absent or ambiguous.
    Window = apps.get_model('fgtida', 'MeetingSubmissionWindow')
    window, _ = Window.objects.using(alias).get_or_create(meeting=meeting,
        defaults={'deadline': datetime(2026, 11, 23, 23, 59, tzinfo=timezone.utc)})
    if window.deadline is None:
        window.deadline = datetime(2026, 11, 23, 23, 59, tzinfo=timezone.utc)
        window.save(using=alias, update_fields=['deadline'])
    apps.get_model('fgtida', 'Contribution').objects.using(alias).filter(meeting__isnull=True,
        created__lte=datetime(2026, 11, 23, 23, 59, tzinfo=timezone.utc)).update(meeting=meeting)
    Group = apps.get_model('group', 'Group')
    titles = {row.acronym.removeprefix('tidawg'): row.name for row in Group.objects.using(alias).filter(acronym__in=['tidawg0', 'tidawg1', 'tidawg2'])}
    Session = apps.get_model('meeting', 'Session')
    for session in Session.objects.using(alias).filter(meeting=meeting):
        group = re.search(r'\bWG(\d+|[A-W])\b', session.name, re.IGNORECASE)
        if not group:
            continue
        number = group[1].upper()
        if re.search(r'\bWG' + re.escape(number) + r'\s*:\s*$', session.name, re.IGNORECASE) and number in titles:
            session.name = session.name.rstrip() + ' ' + titles[number]
        session.agenda_presenters = re.sub(r'\bWG[X-Z]\b', 'WG' + number, session.agenda_presenters)
        session.save(using=alias, update_fields=['name', 'agenda_presenters'])
    Room = apps.get_model('meeting', 'Room')
    room = Room.objects.using(alias).filter(meeting=meeting, name__iexact='ROOM XI').first()
    if room:
        room.name = 'ROOM XI'
        room.save(using=alias, update_fields=['name'])
    else:
        room = Room.objects.using(alias).create(meeting=meeting, name='ROOM XI')
    dinner, _ = Room.objects.using(alias).get_or_create(meeting=meeting, name='TBC')
    slots = apps.get_model('meeting', 'TimeSlot').objects.using(alias)
    Assignment = apps.get_model('meeting', 'SchedTimeSessAssignment')
    for entry in Assignment.objects.using(alias).filter(schedule_id=meeting.schedule_id, session__meeting=meeting).select_related('session'):
        slots.filter(pk=entry.timeslot_id).update(location=dinner if 'dinner' in entry.session.name.lower() else room)


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0042_contribution_meeting_meetingparticipant_and_more')]
    operations = [migrations.RunPython(update_paris, migrations.RunPython.noop)]
