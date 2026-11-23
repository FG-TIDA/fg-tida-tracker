from datetime import date
from io import StringIO
from importlib import import_module
from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from ietf.doc.models import Document
from ietf.group.models import Group
from ietf.meeting.models import Meeting, Session
from . import tests as native_tests
from .models import DocumentAttribution, PublicDocumentType
from .templatetags.fgtida import document_type


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_MAIL_ENABLED=False)
class AgendaTypeTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def test_all_leadership_roles_can_use_a_person_without_a_login_account(self):
        from ietf.person.models import Person, Email
        from ietf.group.models import Role
        person = Person.objects.create(name='External meeting leader', ascii='External meeting leader')
        email = Email.objects.create(address='external-leader@example.test', person=person, origin='Admin')
        group = Group.objects.get(acronym='tidawg0')
        for name in ['chair', 'cochair', 'editor', 'rapporteur']:
            response = self.client.post('/admin/group/role/add/', {
                'group': group.pk, 'name': name, 'person': person.pk, 'email': email.pk})
            self.assertEqual(response.status_code, 302, getattr(response, 'context_data', {}).get('errors'))
        self.assertEqual(Role.objects.filter(group=group, person=person).count(), 4)
        person.refresh_from_db()
        self.assertIsNone(person.user_id)
        self.assertContains(self.client.get('/group/tidawg0/about/'), 'Rapporteur')
        profile_form = self.client.get('/admin/person/person/add/').context['adminform'].form
        self.assertFalse(profile_form.fields['user'].required)

    def test_revised_topics_keep_their_original_session_identifiers(self):
        meeting = Meeting.objects.create(number='paris-topic-test', type_id='tida', date=date(2026, 11, 30), days=2, time_zone='Europe/Paris')
        originals = {}
        for number, wg, topic in [(4, 0, 'Terminology and Use Cases'), (6, 1, 'Continuous Attestation'), (11, 2, 'Identity-Behaviour-Enforcement')]:
            originals[wg] = Session.objects.create(meeting=meeting, group=self.root,
                name=f'Session {number} – WG{wg}: {topic}', purpose_id='regular', type_id='session').pk
        originals[6] = Session.objects.create(meeting=meeting, group=self.root,
            name='Session 16: ICAP', purpose_id='regular', type_id='session').pk
        feedback = Session.objects.create(meeting=meeting, group=self.root,
            name='Session 18 – Feedback and future directions', purpose_id='regular', type_id='session')
        call_command('update_paris_agenda', apply=True, stdout=StringIO())
        for wg, pk in originals.items():
            session = Session.objects.get(pk=pk)
            self.assertTrue(session.on_agenda)
            self.assertIn(f'WG{wg}', session.name)
            self.assertEqual(session.agenda_presenters, 'WGZ Co-Chairs' if wg == 6 else f'Co-Chairs WG{wg}')
        self.assertEqual(meeting.session_set.filter(name__icontains='ICAP').count(), 1)
        feedback.refresh_from_db()
        self.assertEqual(feedback.name, 'Session 18 – Feedback and future directions')
        self.assertTrue(feedback.on_agenda)

    def test_import_is_repeatable_and_keeps_open_ended_event_and_outcomes(self):
        meeting = Meeting.objects.create(number='paris-test', type_id='tida', date=date(2026, 11, 30), days=2, time_zone='Europe/Paris')
        call_command('update_paris_agenda', stdout=StringIO())
        self.assertFalse(meeting.session_set.exists())
        call_command('update_paris_agenda', apply=True, stdout=StringIO())
        identifiers = set(meeting.session_set.values_list('pk', flat=True))
        self.assertEqual(len(identifiers), 27)
        first = meeting.session_set.get(short='paris2026-01')
        self.assertEqual(first.agenda_outcome, 'Agenda adopted')
        self.assertIn('1.2 Address by the French Government', first.agenda_note)
        self.assertEqual(meeting.session_set.filter(agenda_open_ended=True).count(), 0)
        for wg, label in [('WG3', 'WGX Co-Chairs'), ('WG4', 'WGY Co-Chairs'), ('WG5', 'WGZ Co-Chairs'), ('WG6', 'WGZ Co-Chairs')]:
            self.assertEqual(meeting.session_set.get(name__contains=wg + ':').agenda_presenters, label)
        from pathlib import Path
        import json, re
        rows = json.loads(Path('ietf/fgtida/paris_agenda_2026.json').read_text())
        self.assertTrue(all(re.fullmatch(r'\d{2}:\d{2}–\d{2}:\d{2}', row['time']) for row in rows))
        end_of_day = [row for row in rows if row['day'] == 1][-3:]
        self.assertEqual([row['time'] for row in end_of_day], ['16:45–17:00', '17:00–17:30', '17:30–18:15'])
        call_command('update_paris_agenda', apply=True, stdout=StringIO())
        self.assertEqual(identifiers, set(meeting.session_set.values_list('pk', flat=True)))
        page = self.client.get('/meeting/paris-test/agenda/')
        self.assertContains(page, 'Status / expected outcome')
        self.assertContains(page, 'Agenda adopted')
        self.assertContains(self.client.get(f'/admin/meeting/session/{first.pk}/change/'), 'Status / expected outcome')

    def test_categories_migrate_to_types_and_wg0_use_cases_are_contributions(self):
        wg0 = Group.objects.get(acronym='tidawg0')
        proposal = Document.objects.create(name='wg0-example', title='WG0 proposal', type_id='tida', group=wg0)
        proposal.tags.add('tida-use-cases')
        untagged = Document.objects.create(name='wg0-typed-example', title='Typed WG0 example', type_id='tida', group=wg0)
        DocumentAttribution.objects.create(document=untagged, public_type=PublicDocumentType.objects.get(name='Use case'))
        resource = Document.objects.create(name='resource-example', title='Resource example', type_id='tida', group=self.root)
        resource.tags.add('tida-resources')
        import_module('ietf.fgtida.migrations.0037_unify_document_types').migrate_types(apps, connection.schema_editor())
        self.assertEqual(document_type(Document.objects.get(pk=proposal.pk)), 'Contribution')
        self.assertEqual(document_type(Document.objects.get(pk=untagged.pk)), 'Contribution')
        self.assertEqual(document_type(Document.objects.get(pk=resource.pk)), 'Resource')
        kind = PublicDocumentType.objects.get(name='Contribution')
        self.assertContains(self.client.get('/doc/', {'type': f'public-{kind.pk}'}), proposal.title)
        self.assertNotContains(self.client.get('/doc/'), 'Work category')
        self.assertNotContains(self.client.get(f'/doc/{resource.name}/'), '<th scope="row">Category</th>')
        response = self.client.get(f'/admin/doc/document/{resource.pk}/change/')
        self.assertNotIn('tags', response.context['adminform'].form.fields)
        self.assertEqual(self.client.get('/admin/name/doctagname/add/').status_code, 404)

    def test_source_sync_retains_editor_type_override(self):
        from .document_types import assign_source_type
        doc = Document.objects.create(name='type-override', title='Example', type_id='tida', group=self.root)
        assign_source_type(doc, 'themes')
        metadata = DocumentAttribution.objects.get(document=doc)
        self.assertEqual(metadata.public_type.name, 'Theme')
        metadata.public_type = PublicDocumentType.objects.get_or_create(name='Technical note')[0]
        metadata.save()
        assign_source_type(doc, 'resources')
        metadata.refresh_from_db()
        self.assertEqual(metadata.public_type.name, 'Technical note')
