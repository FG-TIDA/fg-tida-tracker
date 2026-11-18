import hashlib
import io
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from ietf.doc.models import Document
from ietf.group.models import Group
from . import core, tests as native_tests
from .contributions import convert_existing_word
from .file_safety import allowed, validate_file
from .models import Contribution, FileScan, DocumentFormat, OutgoingMail, WorkItem
from .source_packages import pack, unpack


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=True,
                   FGTIDA_MAIL_ENABLED=False, FGTIDA_MAIL_IMMEDIATE=False)
class AdminContributionTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def payload(self):
        return {'title': 'Contribution by an external expert',
            'group': Group.objects.get(acronym='tidawg1').pk, 'submission_method': 'rst',
            'contact_name': 'Example Expert', 'contact_email': 'expert@example.test',
            'contact_affiliation': 'Example Research Institute',
            'publication_consent': 'on', 'entry_document': 'index.rst',
            'upload': SimpleUploadedFile('proposal.zip', pack({'index.rst': b'An external contribution.'})), '_save': 'Save'}

    def word(self):
        return subprocess.run(['pandoc', '-f', 'rst', '-t', 'docx'],
            input=b'Original proposal\n=================\n\nA preserved paragraph.', capture_output=True, check=True).stdout

    def test_guest_contribution_metadata_receipt_and_admin_trust_without_scanner(self):
        users = get_user_model().objects.count()
        with patch('ietf.fgtida.file_safety.scan_stream', side_effect=AssertionError('Admin upload must not call antivirus')):
            response = self.client.post('/admin/fgtida/contribution/add/', self.payload())
        self.assertEqual(response.status_code, 302, response.context and response.context['adminform'].form.errors)
        item = Contribution.objects.get()
        self.assertIsNone(item.submitter)
        self.assertEqual(get_user_model().objects.count(), users)
        self.assertEqual(item.document.tida_attribution.authors, 'Example Expert')
        self.assertEqual(item.document.tida_attribution.affiliations, 'Example Research Institute')
        self.assertTrue(allowed(bytes(item.source_archive), 'source.zip'))
        self.assertEqual(FileScan.objects.get(pk=hashlib.sha256(bytes(item.source_archive)).hexdigest()).status, 'admin')
        receipt = OutgoingMail.objects.get(purpose='submitter')
        self.assertEqual(receipt.recipient, 'expert@example.test')
        self.assertIn('Dear Example,', receipt.body)
        self.client.logout()
        page = self.client.get(f'/doc/{item.document.name}/')
        self.assertContains(page, 'Example Expert')
        self.assertContains(page, 'An external contribution.')
        self.assertContains(page, 'Example Research Institute')
        self.client.force_login(self.user)
        attribution = item.document.tida_attribution
        attribution.authors += '\nAnother Author'
        attribution.save(update_fields=['authors'])
        self.assertEqual(self.client.post(f'/admin/fgtida/contribution/{item.pk}/change/', {
            'sequence_number': item.sequence_number, 'contact_name': 'Updated Expert',
            'contact_email': item.contact_email, 'contact_affiliation': 'Updated Institute',
            'review_note': 'Contact corrected.', '_save': 'Save'}).status_code, 302)
        attribution.refresh_from_db()
        self.assertIn('Another Author', attribution.authors)
        self.assertIn('Updated Expert', attribution.authors)
        self.assertNotIn('Example Expert', attribution.authors)

    def test_admin_word_and_derived_package_skip_antivirus_but_keep_original(self):
        word = self.word()
        payload = self.payload()
        payload.update(submission_method='word', entry_document='', upload=SimpleUploadedFile('proposal.docx', word))
        with patch('ietf.fgtida.file_safety.scan_stream', side_effect=AssertionError('No admin AV scan')):
            response = self.client.post('/admin/fgtida/contribution/add/', payload)
        self.assertEqual(response.status_code, 302, response.context and response.context['adminform'].form.errors)
        item = Contribution.objects.get()
        self.assertEqual(bytes(item.content), word)
        self.assertIn(b'preserved paragraph', unpack(bytes(item.source_archive))['index.rst'])
        self.assertTrue(allowed(word, item.filename))
        self.assertTrue(allowed(bytes(item.source_archive), 'source.zip'))
        record = FileScan.objects.get(pk=hashlib.sha256(word).hexdigest())
        self.assertTrue(record.events.filter(operation='admin_upload', actor=self.user).exists())
        from .management.commands.scan_fgtida_files import Command
        command = Command(stdout=io.StringIO())
        command.target, command.force, command.processed = None, True, set()
        with patch('ietf.fgtida.file_safety.scan_stream', side_effect=AssertionError('Maintenance must skip admin uploads')):
            self.assertTrue(command.check(word, item.filename))

    def test_non_admin_and_unrelated_bytes_still_require_antivirus(self):
        user = get_user_model().objects.create_user('ordinary-contributor', password='test-password')
        self.client.force_login(user)
        payload = self.payload()
        payload.pop('_save')
        with tempfile.TemporaryDirectory() as root, override_settings(FGTIDA_QUARANTINE_DIR=root), \
                patch('ietf.fgtida.file_safety.scan_stream', side_effect=OSError) as scan:
            response = self.client.post('/submit/', payload)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(scan.called)
            self.assertFalse(Contribution.objects.exists())
        # Administrator identity alone, without an actual matching HTTP upload,
        # must not silently trust a fetched or previously untrusted file.
        with tempfile.TemporaryDirectory() as root, override_settings(FGTIDA_QUARANTINE_DIR=root), \
                patch('ietf.fgtida.file_safety.scan_stream', side_effect=OSError) as scan:
            with self.assertRaises(ValidationError):
                validate_file(b'Other bytes', 'other.txt')
            scan.assert_called_once()

    @override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=False)
    def test_public_submission_cannot_impersonate_or_set_guest_contributor_fields(self):
        member = get_user_model().objects.create_user('self-submitter', email='member@example.test')
        self.client.force_login(member)
        form = self.client.get('/submit/').context['form']
        for field in ['submitter', 'contact_name', 'contact_email', 'contact_affiliation']:
            self.assertNotIn(field, form.fields)
        payload = self.payload()
        payload['submitter'] = self.user.pk
        response = self.client.post('/submit/', payload)
        self.assertEqual(response.status_code, 302)
        item = Contribution.objects.get()
        self.assertEqual(item.submitter_id, member.pk)
        self.assertEqual((item.contact_name, item.contact_email, item.contact_affiliation), ('', '', ''))
        self.assertFalse(OutgoingMail.objects.filter(recipient='expert@example.test').exists())
        self.assertEqual(self.client.get('/admin/fgtida/contribution/add/').status_code, 302)
        self.client.logout()
        payload = self.payload()
        self.assertEqual(self.client.post('/submit/', payload).status_code, 302)
        self.assertEqual(Contribution.objects.count(), 1)

    def test_admin_cannot_upload_an_unsafe_source_archive(self):
        payload = self.payload()
        payload['upload'] = SimpleUploadedFile('bad.zip', pack({'index.rst': b'Text', 'malware.exe': b'not executable here'}))
        with patch('ietf.fgtida.file_safety.scan_stream', side_effect=AssertionError('No admin AV scan')):
            response = self.client.post('/admin/fgtida/contribution/add/', payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contribution.objects.exists())

    def test_legacy_word_conversion_preserves_reference_version_and_word_download(self):
        word = self.word()
        doc = Document(name='legacy-word-contribution', title='Legacy proposal', type_id='tida', group=self.root)
        with override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=False):
            core.save_document(doc, core.actor_for(self.user), 'Original Word submission.', data=word, filename='original.docx')
        item = Contribution.objects.create(title=doc.title, group=self.root, submitter=None,
            contact_name='External Author', filename='original.docx', content=word, document=doc)
        original_revision, reference = doc.rev, item.reference
        with tempfile.TemporaryDirectory() as root, override_settings(FGTIDA_QUARANTINE_DIR=root), \
                patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')):
            response = self.client.post('/admin/fgtida/contribution/', {
                'action': 'convert_word_contributions', '_selected_action': item.pk})
            self.assertEqual(response.status_code, 302)
            self.assertFalse(convert_existing_word(item.pk, self.user))
        item.refresh_from_db()
        doc.refresh_from_db()
        self.assertEqual(doc.rev, original_revision)
        self.assertEqual(item.reference, reference)
        self.assertEqual(Contribution.objects.count(), 1)
        self.assertEqual(Path(doc.get_file_name()).read_bytes(), word)
        self.assertEqual(bytes(item.content), word)
        self.assertFalse(item.conversion_verified_at)
        package = DocumentFormat.objects.get(document=doc, revision=doc.rev, kind='zip')
        self.assertIn('index.rst', unpack(bytes(package.content)))
        self.client.logout()
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')):
            page = self.client.get(f'/doc/{doc.name}/')
        self.assertContains(page, 'A preserved paragraph.')
        self.assertContains(page, 'RST ZIP package')
        self.assertEqual(self.client.get(f'/document-formats/{package.pk}/download/')['Content-Type'], 'application/zip')

    def test_guest_name_is_required_and_administrator_can_verify_conversion(self):
        payload = self.payload()
        payload['contact_name'] = ''
        response = self.client.post('/admin/fgtida/contribution/add/', payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contribution.objects.exists())
        word = self.word()
        payload = self.payload()
        payload.update(submission_method='word', entry_document='', upload=SimpleUploadedFile('proposal.docx', word))
        self.assertEqual(self.client.post('/admin/fgtida/contribution/add/', payload).status_code, 302)
        item = Contribution.objects.get()
        response = self.client.post(f'/admin/fgtida/contribution/{item.pk}/change/', {
            'sequence_number': item.sequence_number, 'review_note': 'Compared with the original.',
            'contact_name': item.contact_name, 'contact_email': item.contact_email,
            'contact_affiliation': item.contact_affiliation, 'verify_conversion': 'on', '_save': 'Save'})
        self.assertEqual(response.status_code, 302, response.context and response.context['adminform'].form.errors)
        item.refresh_from_db()
        self.assertIsNotNone(item.conversion_verified_at)
        self.assertEqual(item.conversion_verified_by, self.user)
