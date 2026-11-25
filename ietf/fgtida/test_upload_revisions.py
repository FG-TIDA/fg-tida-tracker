import ast
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from . import tests as native_tests
from .contributions import accept
from .models import Contribution, DocumentFormat, OutgoingMail
from .source_packages import pack, unpack
from .test_package_previews import image_bytes
from .test_submission_template import history_table_source


def source(version, body='Updated technical content.'):
    return native_tests.template_source(body).replace(b'Version: 0.0', ('Version: ' + version).encode())


def word(version):
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, 'figure.png').write_bytes(image_bytes('red'))
        text = history_table_source().replace(b'   * - 0.0', ('   * - ' + version).encode())
        text += b'\n.. image:: figure.png\n\nAPI compatibility\n-----------------\n\n.. list-table::\n\n   * - Version\n     - Note\n   * - 9.0\n     - API release\n'
        return subprocess.run(['pandoc', '-f', 'rst', '-t', 'docx'], input=text,
                              cwd=directory, capture_output=True, check=True).stdout


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=False,
                   FGTIDA_MAIL_ENABLED=False, FGTIDA_MAIL_IMMEDIATE=False)
class UploadRevisionTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def initial(self, method='rst'):
        from ietf.group.models import Group
        payload = {'title': 'Upload revision work item', 'group': Group.objects.get(acronym='tidawg1').pk,
                   **self.source_submission()}
        if method == 'word':
            payload.update(submission_method='word', entry_document='', upload=SimpleUploadedFile('original.docx', word('0.0')))
        self.assertEqual(self.client.post('/submit/', payload).status_code, 302)
        return Contribution.objects.select_related('document').get()

    source_submission = native_tests.NativeDatatrackerTests.source_submission

    def revise(self, item, *, method='rst', version='0.1', content=None, **extra):
        upload = content if content is not None else pack({'index.rst': source(version)})
        return self.client.post(f'/submit/{item.pk}/revision/', {
            'submission_method': method, 'version': version, 'publication_consent': 'on',
            'entry_document': 'index.rst' if method == 'rst' else '',
            'upload': SimpleUploadedFile('revised.docx' if method == 'word' else 'revised.zip', upload), **extra})

    def test_rst_revision_keeps_reference_original_and_assets_and_updates_both_previews(self):
        member = get_user_model().objects.create_user('source-owner', first_name='Example', last_name='Owner')
        self.client.force_login(member)
        item = self.initial()
        Contribution.objects.filter(pk=item.pk).update(abstract='Old catalogue abstract.')
        type(item.document).objects.filter(pk=item.document_id).update(abstract='Old catalogue abstract.')
        original, reference, meeting, owner = bytes(item.content), item.reference, item.meeting_id, item.submitter_id
        old_file = Path(item.document.get_file_name())
        old_bytes = old_file.read_bytes()
        notifications = OutgoingMail.objects.count()
        image = image_bytes('red')
        package = pack({'proposal/private-notes.txt': b'Private non-document note.',
                        'proposal/src/doc/index.rst': source('0.1', 'New technical text.\n\n.. image:: figures/a.png\n\nAPI compatibility\n-----------------\n\n| Version | Note |\n|---|---|\n|9.0|API release|'),
                        'proposal/src/doc/figures/a.png': image})
        response = self.revise(item, content=package, entry_document='proposal/src/doc/index.rst')
        self.assertEqual(response.status_code, 302, response.context and response.context['form'].errors)
        item = Contribution.objects.select_related('document').get(pk=item.pk)
        self.assertEqual(Contribution.objects.count(), 1)
        self.assertEqual((item.reference, item.meeting_id, item.submitter_id), (reference, meeting, owner))
        self.assertEqual(bytes(item.content), original)
        self.assertEqual(old_file.read_bytes(), old_bytes)
        files = unpack(bytes(item.source_archive))
        self.assertEqual(files['figures/a.png'], image)
        self.assertIn('conf.py', files)
        self.assertEqual(item.document.tida_versions.get(revision=item.document.rev).version, '0.1')
        raw_original = item.revision_originals.get(revision=item.document.rev)
        self.assertEqual(bytes(raw_original.content), package)
        from types import SimpleNamespace
        from .upload_audit import related_contributions
        self.assertEqual(list(related_contributions(SimpleNamespace(pk=raw_original.sha256)).values_list('pk', flat=True)), [item.pk])
        from django.core.management import call_command
        from io import StringIO
        with override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=True), \
                patch('ietf.fgtida.management.commands.scan_fgtida_files.scanner_version', return_value='Test scanner'), \
                patch('ietf.fgtida.management.commands.scan_fgtida_files.Command.expire_private_uploads'), \
                patch('ietf.fgtida.management.commands.scan_fgtida_files.Command.check', return_value=False) as scan:
            call_command('scan_fgtida_files', force=True, limit=100, stdout=StringIO())
        self.assertTrue(any(call.args[0] == package for call in scan.call_args_list))
        self.assertFalse(DocumentFormat.objects.filter(document=item.document, revision=item.document.rev, kind='zip').exists())
        private_url = f'/submit/{item.pk}/original/{item.document.rev}/'
        self.assertNotContains(self.client.get(response.url), private_url)
        self.assertContains(self.client.get(f'/submit/{item.pk}/'), private_url)
        self.assertEqual(self.client.get(private_url).status_code, 200)
        self.assertNotIn('private-notes.txt', files)
        for url in [response.url, f'/submit/{item.pk}/']:
            page = self.client.get(url)
            self.assertContains(page, 'New technical text.')
            self.assertNotContains(page, 'Old catalogue abstract.')
        self.assertEqual(OutgoingMail.objects.count(), notifications)
        self.assertNotContains(self.client.get(response.url), 'Editorial template adaptation')
        outsider = get_user_model().objects.create_user('other-reader')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(private_url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(private_url).status_code, 302)

    def test_word_revision_after_rst_retains_word_and_requires_fresh_conversion_verification(self):
        item = self.initial()
        original = bytes(item.content)
        revised = word('0.1')
        response = self.revise(item, method='word', content=revised)
        self.assertEqual(response.status_code, 302, response.context and response.context['form'].errors)
        item = Contribution.objects.select_related('document').get(pk=item.pk)
        self.assertEqual(bytes(item.content), original)
        self.assertEqual(bytes(item.document.tida_formats.get(revision=item.document.rev, kind='docx').content), revised)
        from .contribution_packages import original_word
        self.assertEqual(original_word(item), revised)
        from .contributions import convert_existing_word
        with self.assertRaisesMessage(ValidationError, 'revised source'):
            convert_existing_word(item.pk, self.user, rebuild=True, reason='Do not replace the current revision with the initial submission.')
        files = unpack(bytes(item.source_archive))
        self.assertIn(image_bytes('red'), files.values())
        config = ast.parse(files['conf.py'].decode())
        release = next(ast.literal_eval(node.value) for node in config.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'release' for target in node.targets))
        self.assertEqual(release, '0.1')
        page = self.client.get(f'/submit/{item.pk}/')
        self.assertTrue(page.context['can_verify'])
        self.assertContains(page, 'Download the current Word original for comparison')
        with self.assertRaisesMessage(ValidationError, 'verify'):
            accept(item.pk, self.user)
        self.assertEqual(self.client.post(f'/submit/{item.pk}/verify/', {'verified': 'yes', 'revision': '00'}).status_code, 409)
        self.assertEqual(self.client.post(f'/submit/{item.pk}/verify/', {'verified': 'yes', 'revision': item.document.rev}).status_code, 302)
        item.refresh_from_db()
        self.assertIsNotNone(item.conversion_verified_at)
        response = self.revise(item, method='word', version='0.2', content=word('0.2'))
        self.assertEqual(response.status_code, 302)
        item = Contribution.objects.select_related('document').get(pk=item.pk)
        self.assertIsNone(item.conversion_verified_at)
        self.assertTrue(self.client.get(f'/submit/{item.pk}/').context['can_verify'])

    def test_word_to_rst_does_not_require_verifying_an_old_word_conversion(self):
        item = self.initial('word')
        original = bytes(item.content)
        self.assertEqual(self.revise(item).status_code, 302)
        item = Contribution.objects.select_related('document').get(pk=item.pk)
        self.assertEqual(bytes(item.content), original)
        from .contribution_packages import original_word
        self.assertEqual(original_word(item), b'')
        self.assertFalse(self.client.get(f'/submit/{item.pk}/').context['can_verify'])
        accept(item.pk, self.user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'accepted')

    def test_bad_source_wrong_version_and_scan_failure_do_not_change_archives(self):
        item = self.initial()
        before = bytes(item.source_archive)
        response = self.revise(item, content=pack({'index.rst': b'Incomplete draft'}))
        self.assertContains(response, 'Abstract')
        self.assertContains(self.revise(item, version='0.2', content=pack({'index.rst': source('0.1')})), 'must match')
        with patch('ietf.fgtida.submission_sources.prepare_source', side_effect=AssertionError('No conversion for stale versions')):
            self.assertContains(self.revise(item, version='0.0'), 'greater than every archived version')
        with patch('ietf.fgtida.file_safety.validate_file', side_effect=ValidationError('Held by security checks')):
            self.assertContains(self.revise(item), 'Held by security checks')
        item.refresh_from_db()
        self.assertEqual(bytes(item.source_archive), before)
        self.assertEqual(item.document.tida_versions.count(), 1)

    def test_only_submitter_or_authorized_editor_can_upload_a_revision(self):
        item = self.initial()
        other = get_user_model().objects.create_user('unrelated-submitter')
        self.client.force_login(other)
        with patch('ietf.fgtida.submission_sources.prepare_source', side_effect=AssertionError('No parsing for an unauthorized user')):
            self.assertEqual(self.revise(item).status_code, 404)
        self.client.logout()
        self.assertEqual(self.revise(item).status_code, 302)

    def test_revision_form_has_upload_routes_and_no_pdf_route(self):
        item = self.initial()
        page = self.client.get(f'/submit/{item.pk}/revision/')
        self.assertContains(page, 'multipart/form-data')
        self.assertEqual(page.context['form'].initial['submission_method'], 'rst')
        self.assertEqual(set(dict(page.context['form'].fields['submission_method'].choices)), {'rst', 'word', 'github'})
        self.assertContains(self.revise(item, method='word', content=b'%PDF-1.7', upload=SimpleUploadedFile('wrong.pdf', b'%PDF-1.7')),
                            'requires a .docx')
