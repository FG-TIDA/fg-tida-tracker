"""Apply the reviewed 30 November–1 December 2026 agenda without deleting sessions."""
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from ietf.fgtida import core, workflow
from ietf.meeting.models import Session, Schedule, TimeSlot, SchedTimeSessAssignment


class Command(BaseCommand):
    help = 'Preview the Paris agenda update; use --apply after taking a database backup.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--meeting', help='Exact meeting number if date lookup is ambiguous.')

    @transaction.atomic
    def handle(self, *args, **options):
        meetings = core.meetings().filter(date=date(2026, 11, 30))
        if options['meeting']:
            meetings = core.meetings().filter(number=options['meeting'])
        if meetings.count() != 1:
            raise CommandError('Select exactly one Paris meeting using --meeting NUMBER. No data was changed.')
        meeting = meetings.get()
        if meeting.date != date(2026, 11, 30) or meeting.days != 2:
            raise CommandError('The selected meeting must run 30 November–1 December 2026.')
        rows = json.loads(Path(__file__).resolve().parents[2].joinpath('paris_agenda_2026.json').read_text(encoding='utf-8-sig'))
        self.stdout.write(f'{meeting.number}: {len(rows)} agenda rows. ' + ('Applying.' if options['apply'] else 'Preview only; use --apply to save.'))
        actor = core.actor_for() if options['apply'] else None
        if options['apply'] and not meeting.schedule_id:
            meeting.schedule = Schedule.objects.create(meeting=meeting, name='official', owner=actor, public=True, visible=True)
            meeting.save(update_fields=['schedule'])
        matched = set()
        for index, row in enumerate(rows, 1):
            lines = row['event'].splitlines()
            title = lines[0].strip()
            short = f'paris2026-{index:02}'
            # Prefer a prior import key, then the same numbered session. Breaks
            # are matched by local date/time, never by their repeated title alone.
            session = Session.objects.filter(meeting=meeting, short=short).first()
            number = re.match(r'Session (\d+)\b', title)
            if not session and number:
                candidates = Session.objects.filter(meeting=meeting, name__iregex=rf'^Session {number[1]}(?:\D|$)')
                if candidates.count() > 1:
                    raise CommandError(f'Ambiguous session {number[1]}; review existing rows first.')
                session = candidates.first()
            times = re.findall(r'\d{2}[:.]\d{2}', row['time'])
            start = datetime.combine(meeting.date + timedelta(days=row['day']), datetime.strptime(times[0].replace('.', ':'), '%H:%M').time(), ZoneInfo(meeting.time_zone or 'Europe/Paris'))
            end = datetime.combine(start.date(), datetime.strptime(times[1].replace('.', ':'), '%H:%M').time(), start.tzinfo) if len(times) > 1 else None
            if not session:
                entries = SchedTimeSessAssignment.objects.filter(schedule_id=meeting.schedule_id, session__meeting=meeting, timeslot__time=start).exclude(session_id__in=matched)
                if entries.count() == 1:
                    session = entries.first().session
            self.stdout.write(f'{start:%Y-%m-%d %H:%M} {title}')
            if not options['apply']:
                continue
            if not session:
                session = Session(meeting=meeting, group=core.groups().get(acronym='tida'), purpose_id='regular', type_id='session')
            session.name, session.short = title, short
            session.agenda_note = '\n'.join(lines[1:])
            session.agenda_presenters = row['presenters']
            session.agenda_outcome = row['outcome']
            session.agenda_open_ended = end is None
            session.on_agenda = True
            session.save()
            matched.add(session.pk)
            assignments = session.timeslotassignments.filter(schedule=meeting.schedule)
            if assignments.count() > 1:
                raise CommandError(f'{title} has multiple assignments. Review before updating.')
            assignment = assignments.select_related('timeslot').first()
            slot = assignment.timeslot if assignment and not assignment.timeslot.sessionassignments.exclude(pk=assignment.pk).exists() else TimeSlot(meeting=meeting, type_id='session')
            slot.time, slot.name = start, title
            slot.duration = end-start if end else timedelta(0)
            slot.save()
            if assignment:
                assignment.timeslot = slot
                assignment.save(update_fields=['timeslot'])
            else:
                SchedTimeSessAssignment.objects.create(session=session, schedule=meeting.schedule, timeslot=slot)
            if not session.schedulingevent_set.exists():
                workflow.set_session_status(session, 'appr', actor)
        if options['apply']:
            # Keep previous session/material records, but do not mix the old
            # schedule with the replacement agenda. Historical schedules remain.
            old = Session.objects.filter(meeting=meeting).exclude(pk__in=matched)
            self.stdout.write(f'{old.count()} previous unmatched sessions retained off agenda.')
            old.update(on_agenda=False)
            SchedTimeSessAssignment.objects.filter(schedule=meeting.schedule, session__in=old).delete()
