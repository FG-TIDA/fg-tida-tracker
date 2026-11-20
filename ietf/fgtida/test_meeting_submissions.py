from datetime import date, datetime, timedelta, timezone as utc_timezone
from importlib import import_module
from io import StringIO
from unittest.mock import patch
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from ietf.doc.models import Document
from ietf.group.models import Group
from ietf.meeting.models import Meeting, Session, Schedule, TimeSlot, SchedTimeSessAssignment
from . import core, tests as native_tests
from .models import Contribution, MeetingSubmissionWindow, MeetingParticipant, DocumentAttribution
from .meeting_submissions import submissions_open
from .source_packages import pack


class CreditFormatTests(SimpleTestCase):
    def test_identification_formats_preserve_real_values_and_ignore_placeholders(self):
        from .github_attribution import source_attribution
        credits = source_attribution('## 1. Identification\n**Submitting organization:** <Example Lab>\n**Contact name:** <Example Expert>\n**Contact Emails** expert@example.test\n')
        self.assertEqual(credits['authors'], ['Example Expert'])
        self.assertEqual(credits['affiliations'], ['Example Lab'])
        self.assertEqual(credits['contact_emails'], ['expert@example.test'])
        credits = source_attribution('| Theme proposers | First Author, Second Author (Example Lab) |\n')
        self.assertEqual(credits['authors'], ['First Author', 'Second Author'])
        self.assertEqual(credits['affiliations'], ['Example Lab'])
        self.assertFalse(source_attribution('**Contact name:** <Your name>\n**Submitting organization:** <Organization>'))

    def test_plain_and_quoted_declarations_are_read_without_using_issue_accounts(self):
        from .github_attribution import source_attribution
        self.assertEqual(source_attribution('Proposer(s)\nExample Author / Example Lab\nSummary\nBody')['authors'], ['Example Author'])
        self.assertEqual(source_attribution('> - Author: Example Author / Independent Researcher\n')['affiliations'], ['Independent Researcher'])
        self.assertEqual(source_attribution('**Contacts** First Author, Second Author\n')['authors'], ['First Author, Second Author'])
        self.assertEqual(source_attribution('**Source:** Example Author, Example Lab\n**Contact:** author@example.test\n')['authors'], ['Example Author'])


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=False,
                   FGTIDA_MAIL_ENABLED=False, FGTIDA_MAIL_IMMEDIATE=False)
class MeetingSubmissionTests(TestCase):
    def setUp(self):
        native_tests.NativeDatatrackerTests.setUp(self)
        self.meeting = Meeting.objects.create(number='submission-future', title='Future plenary', type_id='tida',
            date=timezone.localdate()+timedelta(days=35), days=2, time_zone='Europe/Paris')
        self.window = MeetingSubmissionWindow.objects.create(meeting=self.meeting, deadline=timezone.now()+timedelta(hours=1))

    def payload(self, **changes):
        return {'title': 'Meeting-linked proposal', 'group': Group.objects.get(acronym='tidawg1').pk,
            'meeting': self.meeting.pk, 'submission_method': 'rst', 'publication_consent': 'on',
            'entry_document': 'index.rst', 'upload': SimpleUploadedFile('source.zip', pack({'index.rst': b'Proposal content.'})), **changes}

    def test_meeting_is_required_and_open_selection_is_archived_with_submission(self):
        missing = self.payload()
        missing.pop('meeting')
        response = self.client.post('/submit/', missing)
        self.assertEqual(response.status_code, 200)
        self.assertIn('meeting', response.context['form'].errors)
        self.assertFalse(Contribution.objects.exists())
        self.assertEqual(self.client.post('/submit/', self.payload()).status_code, 302)
        item = Contribution.objects.get()
        self.assertEqual(item.meeting_id, self.meeting.pk)
        self.assertNotContains(self.client.get(f'/doc/{item.document.name}/'), '<strong>Meeting:</strong>')

    def test_deadline_closes_automatically_and_admin_can_reopen_until_exact_time(self):
        moment = timezone.now()
        with patch('ietf.fgtida.meeting_submissions.timezone.now', return_value=moment+timedelta(hours=2)):
            self.assertFalse(submissions_open(self.meeting))
            response = self.client.post('/submit/', self.payload())
            self.assertEqual(response.status_code, 200)
            self.assertIn('meeting', response.context['form'].errors)
        self.window.deadline = moment-timedelta(minutes=1)
        self.window.reopen_until = moment+timedelta(hours=3)
        self.window.save()
        self.assertEqual(self.client.post('/submit/', self.payload()).status_code, 302)
        with patch('ietf.fgtida.meeting_submissions.timezone.now', return_value=self.window.reopen_until):
            self.assertFalse(submissions_open(Meeting.objects.get(pk=self.meeting.pk)))

    def test_deadline_is_rechecked_after_processing_before_a_contribution_is_created(self):
        from .source_packages import prepare_zip
        def close_during_upload(*args, **kwargs):
            result = prepare_zip(*args, **kwargs)
            MeetingSubmissionWindow.objects.filter(pk=self.window.pk).update(deadline=timezone.now()-timedelta(seconds=1))
            return result
        with patch('ietf.fgtida.source_packages.prepare_zip', side_effect=close_during_upload):
            response = self.client.post('/submit/', self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Submissions for this meeting are closed')
        self.assertFalse(Contribution.objects.exists())

    def test_meeting_search_filters_documents_without_adding_a_result_column(self):
        self.client.post('/submit/', self.payload())
        item = Contribution.objects.get()
        other = Document.objects.create(name='other-meeting-document', title='Other meeting only', type_id='tida', group=self.root)
        response = self.client.get('/doc/', {'meeting': self.meeting.number})
        self.assertContains(response, item.title)
        self.assertNotContains(response, other.title)
        self.assertNotContains(response, '<th scope="col" role="columnheader">Meeting</th>')
        self.assertContains(self.client.get('/'), 'id="doc-meeting"')

    def test_admin_window_form_uses_explicit_utc(self):
        from .meeting_admin import WindowForm
        with timezone.override('Europe/Paris'):
            form = WindowForm(instance=self.window, data={'meeting': self.meeting.pk, 'accepting_submissions': 'on',
                'deadline': '2026-11-23 23:59 UTC', 'reopen_until': ''})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data['deadline'], datetime(2026,11,23,23,59,tzinfo=utc_timezone.utc))
        self.assertContains(self.client.get(f'/admin/meeting/meeting/{self.meeting.pk}/change/'), 'Submission deadline (UTC)')

    def test_participant_table_is_empty_and_only_explicitly_published_rows_appear(self):
        self.client.logout()
        url = f'/meeting/{self.meeting.number}/participants/'
        response = self.client.get(url)
        self.assertContains(response, 'No participants have been published')
        for column in ['Family Name', 'Given Name', 'Organization', 'Country/Territory', 'Reg Type']:
            self.assertContains(response, column)
        row = MeetingParticipant.objects.create(meeting=self.meeting, first_name='Example', last_name='Participant',
            affiliation='Example Lab', country='Example territory', attendance='remote')
        self.assertNotContains(self.client.get(url), 'Example Lab')
        row.published = True
        row.save()
        self.assertContains(self.client.get(url), 'Example Lab')
        self.assertNotContains(self.client.get(url, {'attendance': 'onsite'}), 'Example Lab')

    def test_paris_backfill_corrects_names_rooms_and_preserves_references(self):
        paris = Meeting.objects.create(number='paris-backfill', type_id='tida', date=date(2026,11,30), days=2, time_zone='Europe/Paris')
        actor = core.actor_for(self.user)
        paris.schedule = Schedule.objects.create(meeting=paris, name='official', owner=actor)
        paris.save()
        item = Contribution.objects.create(group=self.root, title='Existing proposal', filename='', content=b'')
        number = item.reference
        for name, presenter in [('Session 6 – WG1:', 'WG1 Co-Chairs'), ('Session 13 – WG3: Discovery', 'WGX Co-Chairs'), ('Dinner', '')]:
            session = Session.objects.create(meeting=paris, name=name, group=self.root, type_id='session', purpose_id='regular', agenda_presenters=presenter)
            slot = TimeSlot.objects.create(meeting=paris, name=name, type_id='session', time=timezone.now())
            SchedTimeSessAssignment.objects.create(session=session, timeslot=slot, schedule=paris.schedule)
        import_module('ietf.fgtida.migrations.0043_paris_submission_meeting').update_paris(apps, connection.schema_editor())
        item.refresh_from_db()
        self.assertEqual(item.meeting_id, paris.pk)
        self.assertEqual(item.reference, number)
        self.assertEqual(paris.submission_window.deadline, datetime(2026,11,23,23,59,tzinfo=utc_timezone.utc))
        self.assertIn(Group.objects.get(acronym='tidawg1').name, paris.session_set.get(short='', name__startswith='Session 6').name)
        self.assertEqual(paris.session_set.get(name__startswith='Session 13').agenda_presenters, 'WG3 Co-Chairs')
        self.assertEqual(set(paris.schedule.assignments.filter(session__name='Dinner').values_list('timeslot__location__name', flat=True)), {'TBC'})
