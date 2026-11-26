from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from django.db import migrations


def adjust_closing_agenda(apps, schema_editor):
    alias = schema_editor.connection.alias
    Meeting = apps.get_model('meeting', 'Meeting')
    candidates = Meeting.objects.using(alias).filter(type_id='tida', date=date(2026,11,30), days=2)
    meeting = candidates.filter(number='tida-2026-11-30-first-focus-group-meeting').first()
    if meeting is None:
        if candidates.count() != 1:
            return
        meeting = candidates.get()
    if not meeting.schedule_id:
        return
    Assignment = apps.get_model('meeting', 'SchedTimeSessAssignment')
    Session = apps.get_model('meeting', 'Session')
    Slot = apps.get_model('meeting', 'TimeSlot')
    zone = ZoneInfo(meeting.time_zone or 'Europe/Paris')
    clock = lambda hour, minute: datetime(2026,12,1,hour,minute,tzinfo=zone)
    official = Assignment.objects.using(alias).filter(schedule_id=meeting.schedule_id, session__meeting_id=meeting.pk)
    # Remove the displayed break, retaining its session and linked materials.
    # Earlier breaks and alternative/historical schedule assignments are untouched.
    breaks = official.filter(session__name__iexact='Coffee break', timeslot__time=clock(16,45))
    break_ids = list(breaks.values_list('session_id', flat=True))
    breaks.delete()
    Session.objects.using(alias).filter(pk__in=break_ids).update(on_agenda=False)
    changes = [('Feedback and future directions', clock(16,45), 30, 'Co-Chairs'),
               ('Trustech dinner party', clock(17,30), 270, '—')]
    for topic, start, minutes, presenters in changes:
        entries = list(official.filter(session__name__icontains=topic,
            timeslot__time__gte=clock(0,0), timeslot__time__lt=clock(0,0)+timedelta(days=1)))
        if len(entries) > 1:
            raise RuntimeError('Ambiguous Paris closing agenda; review official assignments for ' + topic)
        for entry in entries:
            Session.objects.using(alias).filter(pk=entry.session_id).update(
                on_agenda=True, agenda_open_ended=False, agenda_presenters=presenters, agenda_outcome='—')
            slot = Slot.objects.using(alias).get(pk=entry.timeslot_id)
            if slot.time == start and slot.duration == timedelta(minutes=minutes):
                continue
            if Assignment.objects.using(alias).filter(timeslot_id=slot.pk).exclude(pk=entry.pk).exists():
                values = {field.attname: getattr(slot, field.attname) for field in slot._meta.concrete_fields if not field.primary_key}
                values.update(time=start, duration=timedelta(minutes=minutes))
                replacement = Slot.objects.using(alias).create(**values)
                Assignment.objects.using(alias).filter(pk=entry.pk).update(timeslot_id=replacement.pk)
            else:
                Slot.objects.using(alias).filter(pk=slot.pk).update(time=start, duration=timedelta(minutes=minutes))


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0049_contributionrevisionoriginal')]
    operations = [migrations.RunPython(adjust_closing_agenda, migrations.RunPython.noop)]
