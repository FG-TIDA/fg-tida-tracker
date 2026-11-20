import hashlib
import json
import tempfile
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from django.db import transaction
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from ietf.doc.models import Document, DocumentAuthor
from ietf.person.models import Person
from . import core, tests as native_tests
from .credits import resolve_credits
from .github_attribution import source_attribution
from .models import DocumentAttribution, FileScan, FileScanEvent


class AttributionParsingTests(SimpleTestCase):
    def test_structured_identity_is_not_an_author_name(self):
        credits = source_attribution('**Proposer:** Name: Example Person / Organization: Independent expert / Country: Example Country / E-mail: person@example.test\n**Contact name:** Example Person')
        self.assertEqual(credits['authors'], ['Example Person'])
        self.assertEqual(credits['affiliations'], ['Independent expert'])
        self.assertEqual(credits['contact_emails'], ['person@example.test'])

    def test_empty_rst_table_is_not_an_author(self):
        self.assertFalse(source_attribution('Authors\n=======\n| ------- |'))
        self.assertFalse(source_attribution('**Author:**\n| ======= |\n| ------- |'))

    def test_identity_in_markdown_table_and_bold_label(self):
        for body in ('| Proposer(s) | Name: Example Person / Organization: Example Lab / Country: Example Country / E-mail: |',
                     '**Author** Name: Example Person / Organization: Example Lab / Country: Example Country / E-mail:'):
            with self.subTest(body=body):
                credits = source_attribution(body)
                self.assertEqual(credits['authors'], ['Example Person'])
                self.assertEqual(credits['affiliations'], ['Example Lab'])

    def test_multiline_identity_preserves_only_explicit_metadata(self):
        credits = source_attribution('## 1. Identification\nName: Example Person\nOrganization: Example Lab\nCountry: Example Country\nE-mail: person@example.test')
        self.assertEqual(credits['authors'], ['Example Person'])
        self.assertEqual(credits['affiliations'], ['Example Lab'])

    def test_blank_editor_override_does_not_resurrect_source_credits(self):
        metadata = SimpleNamespace(authors='', credit_provenance={'authors': {'mode': 'manual'}})
        credits, explicit, profiles = resolve_credits('Author: Old Author', metadata, {'authors': ['Old Author']}, [])
        self.assertEqual(credits['authors'], [])
        self.assertIn('authors', explicit)


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False)
class CreditEditingTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp
    admin_payload = native_tests.NativeDatatrackerTests.admin_payload

    def document(self):
        doc = Document(name='credit-accuracy-test', title='Metadata accuracy example', group=self.root, type_id='tida')
        core.save_document(doc, core.actor_for(), 'Source import', data=b'Author: Original Author\nOrganization: Original Lab', filename='source.txt')
        return doc

    def test_editor_override_survives_repair_and_explicit_clear(self):
        from .catalog_repairs import repair_catalog
        doc = self.document()
        meta = DocumentAttribution.objects.create(document=doc, authors='Original Author',
            credit_provenance={'authors': {'mode': 'source', 'value': 'Original Author'}})
        edit = f'/admin/doc/document/{doc.pk}/change/'
        for value in ('Edited Author', ''):
            payload = self.admin_payload(edit, {'name': doc.name, 'title': doc.title, 'type': 'tida', 'group': self.root.pk,
                'comment_about_changes': 'Correct author metadata'})
            payload.update({'tida_attribution-TOTAL_FORMS': '1', 'tida_attribution-INITIAL_FORMS': '1',
                'tida_attribution-0-id': str(meta.pk), 'tida_attribution-0-document': str(doc.pk),
                'tida_attribution-0-authors': value})
            response = self.client.post(edit, payload)
            self.assertEqual(response.status_code, 302)
            repair_catalog(apply=True)
            meta.refresh_from_db()
            self.assertEqual(meta.authors, value)
            self.assertEqual(meta.credit_provenance['authors']['mode'], 'manual')
            page = self.client.get(f'/doc/{doc.name}/')
            self.assertContains(page, '<th scope="row">Author(s)</th>')
            self.assertContains(page, value or 'Not entered')
            self.assertEqual(page.context['authors'], [])

    def test_profile_author_edit_takes_precedence_over_stale_import(self):
        doc = self.document()
        original = Person.objects.create(name='First Person', ascii='First Person')
        revised = Person.objects.create(name='Correct Person', ascii='Correct Person')
        author = DocumentAuthor.objects.create(document=doc, person=original, order=1, affiliation='Old Lab')
        DocumentAttribution.objects.create(document=doc, authors='Stale imported name', affiliations='Stale Lab')
        edit = f'/admin/doc/document/{doc.pk}/change/'
        payload = self.admin_payload(edit, {'name': doc.name, 'title': doc.title, 'type': 'tida', 'group': self.root.pk,
            'comment_about_changes': 'Correct assigned author'})
        payload.update({'documentauthor_set-TOTAL_FORMS': '1', 'documentauthor_set-INITIAL_FORMS': '1',
            'documentauthor_set-0-id': str(author.pk), 'documentauthor_set-0-document': str(doc.pk),
            'documentauthor_set-0-person': str(revised.pk), 'documentauthor_set-0-order': '1',
            'documentauthor_set-0-affiliation': 'Correct Lab'})
        response = self.client.post(edit, payload)
        self.assertEqual(response.status_code, 302)
        page = self.client.get(f'/doc/{doc.name}/')
        self.assertContains(page, 'Correct Person')
        self.assertContains(page, 'Correct Lab')
        self.assertNotContains(page, 'Stale imported name')

    def test_legacy_separator_metadata_is_repaired_without_inventing_a_person(self):
        from .catalog_repairs import repair_catalog
        doc = self.document()
        meta = DocumentAttribution.objects.create(document=doc, authors='| ======= |\n| ------- |')
        repair_catalog(apply=True)
        meta.refresh_from_db()
        self.assertEqual(meta.authors, 'Original Author')

    def test_contact_sync_cannot_restore_an_explicitly_cleared_author(self):
        from .models import Contribution
        from .contributions import sync_contact_metadata
        doc = self.document()
        meta = DocumentAttribution.objects.create(document=doc, authors='',
            credit_provenance={'authors': {'mode': 'manual'}})
        item = Contribution.objects.create(document=doc, group=self.root, title=doc.title,
            filename='', content=b'', contact_name='Submission Contact')
        sync_contact_metadata(item)
        meta.refresh_from_db()
        self.assertEqual(meta.authors, '')
        sync_contact_metadata(item, clear=True)
        meta.refresh_from_db()
        self.assertEqual(meta.authors, 'Submission Contact')

    def test_upload_audit_finds_rescanned_records_and_never_changes_status(self):
        from .models import Contribution
        with tempfile.TemporaryDirectory() as directory, override_settings(FGTIDA_QUARANTINE_DIR=directory):
            item = Contribution.objects.create(document=self.document(), group=self.root, title='Old submission', filename='original.zip', content=b'example')
            record = FileScan.objects.create(sha256=hashlib.sha256(b'example').hexdigest(), filename='source.zip', size=7,
                status='clean', checked=timezone.now())
            FileScan.objects.filter(pk=record.pk).update(created=timezone.now()-timedelta(days=10))
            FileScanEvent.objects.create(scan=record, operation='review', status='pending', detail='Fresh scan requested')
            output = StringIO()
            call_command('audit_fgtida_uploads', since=(timezone.now()-timedelta(days=1)).strftime('%Y-%m-%d'),
                until=(timezone.now()+timedelta(days=1)).strftime('%Y-%m-%d'), match_originals=True, stdout=output)
            self.assertIn(record.pk, output.getvalue())
            self.assertIn('Passed checks', output.getvalue())
            report = json.JSONDecoder().raw_decode(output.getvalue())[0]
            self.assertEqual(report['scans'][0]['contributions'][0]['pk'], item.pk)
            record.refresh_from_db()
            self.assertEqual(record.status, 'clean')
            page = self.client.get(f'/admin/fgtida/filescan/{record.pk}/change/')
            self.assertContains(page, 'No saved contribution association found')

    @override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=True)
    def test_clean_original_is_retained_until_verified_archive_commit(self):
        from .file_safety import validate_file, quarantine_path
        data = b'Author: Example Author\nA recoverable document.'
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory, override_settings(FGTIDA_QUARANTINE_DIR=directory):
            with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')), \
                    patch('ietf.fgtida.file_safety.inspect_publication'):
                validate_file(data, 'original.txt')
            self.assertTrue(quarantine_path(digest).is_file())
            doc = Document(name='committed-archive-test', title='Committed original', type_id='tida', group=self.root)
            with self.captureOnCommitCallbacks(execute=True):
                core.save_document(doc, core.actor_for(), 'Save original', data=data, filename='original.txt')
                self.assertTrue(quarantine_path(digest).is_file())
            self.assertFalse(quarantine_path(digest).exists())
            self.assertTrue(FileScanEvent.objects.filter(scan_id=digest, operation='archived').exists())

    @override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=True)
    def test_failed_archive_does_not_consume_clean_original(self):
        from .file_safety import validate_file, quarantine_path
        data = b'Example original awaiting recovery.'
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory, override_settings(FGTIDA_QUARANTINE_DIR=directory):
            with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')), \
                    patch('ietf.fgtida.file_safety.inspect_publication'):
                validate_file(data, 'original.txt')
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(RuntimeError), transaction.atomic():
                    doc = Document(name='failed-archive-test', title='Failed original', type_id='tida', group=self.root)
                    core.save_document(doc, core.actor_for(), 'Save original', data=data, filename='original.txt')
                    raise RuntimeError('Submission failed before commit')
            self.assertTrue(quarantine_path(digest).is_file())
            self.assertFalse(Document.objects.filter(name='failed-archive-test').exists())

    @override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=True)
    def test_quarantine_cleanup_verifies_database_original_before_removing_copy(self):
        from .file_safety import validate_file, quarantine_path, retire_private_after_archive
        from .models import Contribution
        data = b'Original submitted Word or source file.'
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory, override_settings(FGTIDA_QUARANTINE_DIR=directory):
            with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')), \
                    patch('ietf.fgtida.file_safety.inspect_publication'):
                validate_file(data, 'original.txt')
            item = Contribution.objects.create(group=self.root, title='Stored original', filename='original.txt', content=b'Different file')
            with self.captureOnCommitCallbacks(execute=True):
                retire_private_after_archive(data, 'original.txt', contribution_id=item.pk)
            self.assertTrue(quarantine_path(digest).exists())
            Contribution.objects.filter(pk=item.pk).update(content=data)
            with self.captureOnCommitCallbacks(execute=True):
                retire_private_after_archive(data, 'original.txt', contribution_id=item.pk)
            self.assertFalse(quarantine_path(digest).exists())
