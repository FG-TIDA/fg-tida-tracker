from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import tempfile

from django.core.management import call_command
from django.test import TestCase, override_settings
from ietf.doc.models import Document
from ietf.meeting.models import Meeting, Session, Room, Schedule, TimeSlot, SchedTimeSessAssignment, SessionPresentation
from . import tests as native_tests, core
from .models import MeetingParticipant, DocumentListing, SessionResource


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=False, FGTIDA_MAIL_ENABLED=False)
class MeetingRefreshTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp
    admin_payload = native_tests.NativeDatatrackerTests.admin_payload

    def test_admin_liaison_fields_save_through_the_document_admin(self):
        from ietf.doc.models import State
        from django.core.files.uploadedfile import SimpleUploadedFile
        values = self.admin_payload('/admin/doc/document/add/', {
            'name': 'admin-liaison-detail-test', 'title': 'Admin liaison record', 'type': 'tida', 'group': self.root.pk,
            'lifecycle_state': State.objects.get(type_id='tida', slug='submitted').pk,
            'comment_about_changes': 'Record the approved liaison metadata.', 'document_direction': 'output',
            'document_source': 'FG-TIDA', 'document_destination': 'Example SDO',
            'document_posted_date': '2026-11-27', 'document_deadline': '2026-12-31',
            'document_purpose': 'For information', 'document_body': 'Public liaison message.', '_save': 'Save'})
        values['upload'] = SimpleUploadedFile('liaison.rst', b'Liaison message\n===============\n\nFor information.\n', content_type='text/plain')
        result = self.client.post('/admin/doc/document/add/', values)
        self.assertEqual(result.status_code, 302, result.context and result.context['adminform'].form.errors)
        record = DocumentListing.objects.get(document__name='admin-liaison-detail-test')
        self.assertEqual(record.destination, 'Example SDO')
        self.assertEqual(record.deadline, date(2026, 12, 31))
        self.assertEqual(record.body, 'Public liaison message.')

    def meeting(self):
        return Meeting.objects.create(number='refresh-test', type_id='tida', title='Paris meeting', date=date(2026, 11, 30), days=2, time_zone='Europe/Paris')

    def test_all_participants_are_on_one_page_and_sort_both_directions(self):
        meeting = self.meeting()
        MeetingParticipant.objects.bulk_create([
            MeetingParticipant(meeting=meeting, full_name=f'Participant {number:03}', published=True)
            for number in range(105)])
        self.client.logout()
        url = f'/meeting/{meeting.number}/participants/'
        response = self.client.get(url, {'page': '2'})
        self.assertEqual(response.context['filtered_count'], 105)
        self.assertContains(response, 'Participant 000')
        self.assertContains(response, 'Participant 104')
        self.assertNotContains(response, 'Participant pages')
        self.assertContains(response, 'aria-sort="ascending"')
        descending = self.client.get(url, {'sort': '-name'})
        self.assertEqual(descending.context['participants'][0].full_name, 'Participant 104')
        self.assertContains(descending, 'aria-sort="descending"')

    def test_participant_csv_import_is_public_field_only_idempotent_and_keeps_known_attendance(self):
        meeting = self.meeting()
        known = MeetingParticipant.objects.create(meeting=meeting, full_name='Example Known', affiliation='Example Lab', country='France', attendance='onsite')
        data = 'Full Name,Organitzation, Country\nExample Known,Example Lab,France\nExample New,Other Lab,Germany\nExample New,Other Lab,Germany\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'registrations.csv'
            path.write_text(data, encoding='utf-8-sig')
            output = StringIO()
            call_command('import_fgtida_participants', meeting=meeting.number, csv_path=str(path), publish=True, stdout=output)
            self.assertEqual(meeting.tida_participants.count(), 1)
            for _ in range(2):
                call_command('import_fgtida_participants', meeting=meeting.number, csv_path=str(path), apply=True, publish=True, stdout=output)
            self.assertNotIn('Example Known', output.getvalue())
            self.assertIn('1 duplicate CSV rows skipped', output.getvalue())
        known.refresh_from_db()
        self.assertEqual(known.attendance, 'onsite')
        self.assertTrue(known.published)
        added = meeting.tida_participants.get(full_name='Example New')
        self.assertEqual((added.first_name, added.last_name, added.attendance), ('', '', ''))
        self.assertEqual(meeting.tida_participants.count(), 2)
        MeetingParticipant.objects.create(meeting=meeting, full_name='Unpublished Person', affiliation='Hidden Org', country='Hidden Country')
        self.client.logout()
        page = self.client.get(f'/meeting/{meeting.number}/participants/')
        self.assertEqual(page.context['total'], 2)
        self.assertEqual(page.context['country_count'], 2)
        self.assertContains(page, 'Full Name')
        self.assertNotContains(page, 'Family Name')
        self.assertNotContains(page, 'Hidden Org')
        self.assertEqual(self.client.get(f'/meeting/{meeting.number}/participants/?attendance=unknown').context['filtered_count'], 1)
        self.assertContains(self.client.get(f'/meeting/{meeting.number}/participants/?q=Germany'), 'Example New')
        self.assertEqual(self.client.post(f'/meeting/{meeting.number}/participants/').status_code, 405)

    def test_liaison_filters_details_and_unpublished_document_boundary(self):
        incoming = Document.objects.create(name='incoming-liaison-test', title='Incoming example liaison', type_id='tida', group=self.root)
        outgoing = Document.objects.create(name='outgoing-liaison-test', title='Outgoing example liaison', type_id='tida', group=self.root)
        hidden = Document.objects.create(name='hidden-liaison-test', title='Hidden liaison', type_id='tida', group=self.root)
        hidden.tags.add('tida-uncatalogued')
        listing = DocumentListing.objects.create(document=incoming, direction='input', source='Example Standards Body', destination='FG-TIDA',
            posted_date=date(2026, 11, 20), deadline=date(2026, 12, 15), purpose='For information', body='Unique technical liaison phrase.')
        DocumentListing.objects.create(document=outgoing, direction='output', source='FG-TIDA', destination='Other Body', posted_date=date(2026, 11, 25))
        private = DocumentListing.objects.create(document=hidden, direction='input')
        self.client.logout()
        page = self.client.get('/liaison/')
        self.assertContains(page, f'/liaison/{listing.pk}/')
        self.assertNotContains(page, 'Hidden liaison')
        filtered = self.client.get('/liaison/', {'direction': 'input', 'source': 'Standards', 'destination': 'FG-TIDA', 'start_date': '2026-11-20', 'end_date': '2026-11-20', 'q': 'technical liaison'})
        self.assertEqual(filtered.context['page'].paginator.count, 1)
        self.assertNotContains(filtered, outgoing.title)
        detail = self.client.get(f'/liaison/{listing.pk}/')
        self.assertContains(detail, '2026-12-15')
        self.assertContains(detail, 'For information')
        self.assertContains(detail, 'Unique technical liaison phrase.')
        self.assertEqual(self.client.get(f'/liaison/{private.pk}/').status_code, 404)
        invalid = self.client.get('/liaison/?start_date=2026-12-01&end_date=2026-01-01')
        self.assertContains(invalid, 'End date must be on or after start date.')

    def test_materials_page_and_partial_keep_agenda_slides_minutes_together(self):
        meeting = self.meeting()
        session = Session.objects.create(meeting=meeting, group=self.root, purpose_id='regular', type_id='session', name='Detailed session', agenda_note='1.1 Welcome\n\n1.2 Discuss the charter')
        agenda = Document.objects.create(name='refresh-session-agenda', title='Session agenda', type_id='agenda', group=self.root)
        slides = Document.objects.create(name='refresh-session-slides', title='Example presentation', type_id='slides', group=self.root)
        minutes = Document.objects.create(name='refresh-session-minutes', title='Example minutes', type_id='minutes', group=self.root)
        for doc in (agenda, slides, minutes):
            SessionPresentation.objects.create(session=session, document=doc)
        schedule = Schedule.objects.create(meeting=meeting, name='official', owner=core.actor_for())
        meeting.schedule = schedule; meeting.save(update_fields=['schedule'])
        slot = TimeSlot.objects.create(meeting=meeting, type_id='session', name='Session', time=datetime(2026, 11, 30, 11, tzinfo=timezone.utc), duration=timedelta(minutes=45), location=Room.objects.create(meeting=meeting, name='ROOM XI'))
        SchedTimeSessAssignment.objects.create(schedule=schedule, timeslot=slot, session=session)
        self.client.logout()
        page = self.client.get(f'/session/{session.pk}/materials/')
        for text in ('1.1 Welcome', 'Example presentation', 'Example minutes', 'ROOM XI', 'fgtida-room-badge', 'XI'):
            self.assertContains(page, text)
        fragment = self.client.get(f'/session/{session.pk}/materials/?partial=1&tab=slides')
        self.assertNotContains(fragment, '<html')
        self.assertContains(fragment, 'session-material-tab active')
        self.assertEqual(fragment['Cache-Control'], 'no-store')
        self.assertEqual(self.client.post(f'/session/{session.pk}/materials/').status_code, 405)

    def test_paris_v03_split_wg2_timing_and_material_identity_survive_repeated_updates(self):
        meeting = Meeting.objects.create(number='paris-v03-test', type_id='tida', date=date(2026, 11, 30), days=2, time_zone='Europe/Paris')
        old = Session.objects.create(meeting=meeting, group=self.root, purpose_id='regular', type_id='session', name='Session 4 – WG2: Identity, Behaviour and Enforcement for AI Agents (IBEA)')
        material = Document.objects.create(name='kept-wg2-material', title='Keep original presentation', type_id='slides', group=self.root)
        SessionPresentation.objects.create(session=old, document=material)
        for _ in range(2):
            call_command('update_paris_agenda', meeting=meeting.number, apply=True, stdout=StringIO())
        old.refresh_from_db()
        self.assertIn('part 1', old.name)
        self.assertTrue(SessionPresentation.objects.filter(session=old, document=material).exists())
        wg2 = list(meeting.session_set.filter(on_agenda=True, name__contains='WG2').order_by('name'))
        self.assertEqual(len(wg2), 2)
        self.assertIn('12:25–12:35', wg2[0].agenda_note)
        self.assertIn('14:00–14:10', wg2[1].agenda_note)
        self.assertEqual(meeting.session_set.filter(on_agenda=True).count(), 30)
        page = self.client.get(f'/meeting/{meeting.number}/agenda/')
        self.assertContains(page, 'Show meeting materials')
        self.assertContains(page, 'colspan="3"')
        self.assertNotContains(page, '<th scope="col">Lead / Presenter</th>')
        self.assertContains(page, 'Mariagrazia SQUICCIARINI')
        self.assertContains(page, '17:45')
