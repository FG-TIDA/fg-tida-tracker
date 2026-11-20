import hashlib
from importlib import import_module
from pathlib import Path
import tempfile
from unittest.mock import patch
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from . import core, tests as native_tests
from .file_safety import validate_file, allowed
from .models import FileScan
from .source_packages import pack
from ietf.doc.models import Document


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=True, FGTIDA_MAIL_ENABLED=False)
class PublicationChecksTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def test_clean_original_downloads_even_when_preview_cannot_render(self):
        samples = [('unrenderable.pdf', b'Legacy PDF not readable by this preview parser.'),
                   ('legacy.zip', pack({'README.txt': b'A supporting package without an RST entry point.'}))]
        with tempfile.TemporaryDirectory() as root, override_settings(FGTIDA_QUARANTINE_DIR=root), \
                patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')):
            for index, (filename, data) in enumerate(samples):
                doc = Document(name=f'clean-original-{index}', title='Checked original', type_id='tida', group=self.root)
                core.save_document(doc, core.actor_for(self.user), 'Archive the clean original.', data=data, filename=filename)
                self.assertTrue(allowed(data, filename))
                self.assertEqual(Path(doc.get_file_name()).read_bytes(), data)
                response = self.client.get(f'/doc/{doc.name}/')
                self.assertContains(response, 'A reading preview is unavailable')
                self.assertNotContains(response, 'awaiting security checks')
                download = self.client.get(f'/doc/{doc.name}/download/0.0/')
                self.assertEqual(download.status_code, 200)
                self.assertEqual(b''.join(download.streaming_content), data)

    def test_archive_limits_and_antivirus_findings_still_block_files(self):
        with tempfile.TemporaryDirectory() as root, override_settings(FGTIDA_QUARANTINE_DIR=root), \
                patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')) as scanner:
            validate_file(b'Older Word original for download.', 'legacy.doc')
            self.assertTrue(allowed(b'Older Word original for download.', 'legacy.doc'))
            oversized = pack({'large.txt': b'0' * (31 * 1024 * 1024)})
            with self.assertRaises(ValidationError):
                validate_file(oversized, 'too-large.zip')
            self.assertFalse(allowed(oversized, 'too-large.zip'))
            scanner.return_value = ('ClamAV test', 'Example.Malware')
            with self.assertRaises(ValidationError):
                validate_file(b'Blocked malware test bytes', 'infected.pdf')
            self.assertFalse(allowed(b'Blocked malware test bytes', 'infected.pdf'))
            scanner.side_effect = OSError('Unavailable scanner')
            with self.assertRaises(ValidationError):
                validate_file(b'No scan completed', 'unscanned.pdf')
            self.assertFalse(allowed(b'No scan completed', 'unscanned.pdf'))

    def test_supporting_build_files_do_not_block_rst_reading_and_active_images_stay_blocked(self):
        from .package_previews import image_response
        from django.core.exceptions import PermissionDenied
        data = pack({'index.rst': b'Complete contribution\n=====================\n\nReadable source content.',
            'build.js': b'An inert supporting file, not executed by the tracker.',
            'diagram.svg': b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'})
        with tempfile.TemporaryDirectory() as root, override_settings(FGTIDA_QUARANTINE_DIR=root), \
                patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')):
            doc = Document(name='complete-source-package', title='Complete contribution', type_id='tida', group=self.root)
            core.save_document(doc, core.actor_for(self.user), 'Publish checked source.', data=data, filename='complete.zip')
            self.assertContains(self.client.get(f'/doc/{doc.name}/'), 'Readable source content.')
            self.assertTrue(allowed(data, 'complete.zip'))
            with self.assertRaises(PermissionDenied):
                image_response(data, 'diagram.svg')

    def test_old_document_holds_requeue_and_only_release_after_fresh_scan(self):
        data = b'Legacy content'
        digest = hashlib.sha256(data).hexdigest()
        held = FileScan.objects.create(sha256=digest, filename='held.pdf', size=len(data), status='blocked', rejected_formats=['pdf'])
        older = FileScan.objects.create(sha256='2'*64, filename='older.zip', size=10, status='blocked')
        rejected = FileScan.objects.create(sha256='1'*64, filename='malware.pdf', size=10, status='rejected')
        import_module('ietf.fgtida.migrations.0041_recheck_document_holds').requeue(apps, connection.schema_editor())
        held.refresh_from_db()
        rejected.refresh_from_db()
        older.refresh_from_db()
        self.assertEqual(held.status, 'pending')
        self.assertEqual(rejected.status, 'rejected')
        self.assertEqual(older.status, 'pending')
        self.assertFalse(allowed(data, 'held.pdf'))
        with tempfile.TemporaryDirectory() as root, override_settings(FGTIDA_QUARANTINE_DIR=root), \
                patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')) as scanner:
            validate_file(data, 'held.pdf')
            scanner.assert_called_once_with(data)
        self.assertTrue(allowed(data, 'held.pdf'))
