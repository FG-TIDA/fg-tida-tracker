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

    def test_import_is_repeatable_and_keeps_open_ended_event_and_outcomes(self):
        meeting = Meeting.objects.create(number='paris-test', type_id='tida', date=date(2026, 11, 30), days=2, time_zone='Europe/Paris')
        call_command('update_paris_agenda', stdout=StringIO())
        self.assertFalse(meeting.session_set.exists())
        call_command('update_paris_agenda', apply=True, stdout=StringIO())
        identifiers = set(meeting.session_set.values_list('pk', flat=True))
        self.assertEqual(len(identifiers), 25)
        first = meeting.session_set.get(short='paris2026-01')
        self.assertEqual(first.agenda_outcome, 'Agenda adopted')
        self.assertIn('1.2 Address by the French Government', first.agenda_note)
        self.assertEqual(meeting.session_set.filter(agenda_open_ended=True).count(), 1)
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
