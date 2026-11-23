"""Apply the reviewed 30 November–1 December 2026 agenda without deleting sessions."""
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from ietf.doc.models import Document
from ietf.fgtida import core, workflow
from ietf.meeting.models import Session, Schedule, TimeSlot, SchedTimeSessAssignment, Room


class Command(BaseCommand):
    help = 'Preview the Paris agenda update; use --apply after taking a database backup.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--meeting', help='Exact meeting number if date lookup is ambiguous.')
        parser.add_argument('--agenda-file', help='Reviewed replacement DOCX agenda file, retained as a new document version.')
        parser.add_argument('--agenda-document', help='Exact existing agenda document identifier when lookup is ambiguous.')

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
        replacement = agenda_document = None
        if options['agenda_file']:
            source = Path(options['agenda_file'])
            if source.suffix.lower() != '.docx' or not source.is_file() or source.stat().st_size > 10 * 1024 * 1024:
                raise CommandError('Select an existing DOCX agenda of at most 10 MB.')
            replacement = source.read_bytes()
            from ietf.fgtida.file_validation import validate_office
            validate_office(replacement, '.docx')
            choices = Document.objects.filter(session__meeting=meeting).distinct()
            if options['agenda_document']:
                choices = choices.filter(name=options['agenda_document'])
            else:
                choices = choices.filter(Q(title__icontains='agenda') | Q(type_id__in=['agenda', 'tida-agenda']))
            if choices.count() != 1:
                raise CommandError('Select exactly one existing meeting agenda using --agenda-document IDENTIFIER; no document was replaced.')
            agenda_document = choices.get()
            self.stdout.write('Replacement agenda document: ' + agenda_document.name)
        self.stdout.write(f'{meeting.number}: {len(rows)} agenda rows. ' + ('Applying.' if options['apply'] else 'Preview only; use --apply to save.'))
        actor = core.actor_for() if options['apply'] else None
        if options['apply']:
            room = Room.objects.filter(meeting=meeting, name__iexact='ROOM XI').first()
            if not room:
                room = Room.objects.create(meeting=meeting, name='ROOM XI')
            elif room.name != 'ROOM XI':
                room.name = 'ROOM XI'
                room.save(update_fields=['name'])
            dinner_room, _ = Room.objects.get_or_create(meeting=meeting, name='TBC')
        if options['apply'] and not meeting.schedule_id:
            meeting.schedule = Schedule.objects.create(meeting=meeting, name='official', owner=actor, public=True, visible=True)
            meeting.save(update_fields=['schedule'])
        matched = set()
        for index, row in enumerate(rows, 1):
            lines = row['event'].splitlines()
            title = lines[0].strip()
            short = f'paris2026-{index:02}'
            # A revised agenda can move topics AND reuse session numbers. Match
            # WG topics before row positions, preserving their linked materials.
            candidates = Session.objects.filter(meeting=meeting).exclude(pk__in=matched)
            if candidates.filter(on_agenda=True).exists():
                candidates = candidates.filter(on_agenda=True)
            session = candidates.filter(name=title).first() if title.lower() not in {'coffee break', 'lunch'} else None
            if row.get('match_topic'):
                topic_matches = candidates.filter(Q(name__icontains=row['match_topic']) | Q(agenda_note__icontains=row['match_topic']))
                if topic_matches.count() > 1:
                    raise CommandError('Ambiguous topic ' + row['match_topic'] + '; review existing rows first.')
                session = topic_matches.first()
            if not session and 'ICAP' in title:
                icap = candidates.filter(name__icontains='ICAP')
                if icap.count() > 1:
                    raise CommandError('Ambiguous ICAP session; review existing rows first.')
                session = icap.first()
            wg = re.search(r'\bWG([0-9]+|[A-Z])\b', row['event'])
            if not session and wg:
                topics = candidates.filter(name__iregex=rf'(^|[^A-Za-z0-9])WG{wg[1]}([^A-Za-z0-9]|$)')
                topics = topics.filter(name__icontains='fireside') if 'fireside' in title.lower() else topics.exclude(name__icontains='fireside')
                if topics.count() > 1:
                    raise CommandError(f'Ambiguous WG{wg[1]} session; review existing rows first.')
                session = topics.first()
            number = re.match(r'Session (\d+)\b', title)
            if not session and number and not wg:
                topic = re.sub(r'^Session \d+\s*[:–-]\s*', '', title)
                by_topic = candidates.filter(name__iendswith=topic)
                if by_topic.count() > 1:
                    raise CommandError(f'Ambiguous topic {topic}; review existing rows first.')
                session = by_topic.first()
            if not session and number and not wg and 'ICAP' not in title:
                candidates = Session.objects.filter(meeting=meeting, name__iregex=rf'^Session {number[1]}(?:\D|$)')
                candidates = candidates.exclude(pk__in=matched)
                if candidates.count() > 1:
                    raise CommandError(f'Ambiguous session {number[1]}; review existing rows first.')
                session = candidates.first()
            times = re.findall(r'\d{2}[:.]\d{2}', row['time'])
            start = datetime.combine(meeting.date + timedelta(days=row['day']), datetime.strptime(times[0].replace('.', ':'), '%H:%M').time(), ZoneInfo(meeting.time_zone or 'Europe/Paris'))
            end = datetime.combine(start.date(), datetime.strptime(times[1].replace('.', ':'), '%H:%M').time(), start.tzinfo) if len(times) > 1 else None
            if not session:
                entries = SchedTimeSessAssignment.objects.filter(schedule_id=meeting.schedule_id, session__meeting=meeting, session__name=title, timeslot__time=start).exclude(session_id__in=matched)
                if entries.count() == 1:
                    session = entries.first().session
            self.stdout.write(f'{start:%Y-%m-%d %H:%M} {title}')
            if session:
                matched.add(session.pk)
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
            if not slot.location_id:
                slot.location = dinner_room if 'dinner' in title.lower() else room
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
            if replacement is not None:
                current = Path(agenda_document.get_file_name()) if agenda_document.uploaded_filename else None
                if current and current.is_file() and current.read_bytes() == replacement:
                    self.stdout.write('The current downloadable agenda already matches this DOCX; no extra version created.')
                else:
                    core.save_document(agenda_document, actor, 'Paris agenda replaced with the reviewed 23 November DCO revision.',
                        data=replacement, filename='FG-TIDA_1st_meeting_agenda_revised_Nov_23_DCO.docx')
                    self.stdout.write('Downloadable agenda updated; earlier document versions retained.')
