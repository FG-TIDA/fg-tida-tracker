import hashlib
import io
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from ietf.doc.models import Document
from . import core, tests as native_tests
from .models import FileScan, TrainingVideo, TrainingVideoChunk


class MaintenanceCadenceTests(SimpleTestCase):
    def test_runtime_report_uses_fixed_labels_without_disclosing_arguments(self):
        from deploy.runtime_report import label
        self.assertEqual(label(b'python\0ietf/manage.py\0scan_fgtida_files\0--secret=private\0'), 'job:scan_fgtida_files')
        self.assertEqual(label(b'/private/user/path\0personal-data\0'), 'other')

    def test_retries_do_not_walk_archive_corpus_each_minute(self):
        from deploy import maintenance
        clock = [1000.0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 4:
                raise StopIteration
            clock[0] += 900 if len(sleeps) == 3 else seconds
        with patch.object(maintenance, 'invoke', return_value=True) as invoke, \
                patch.object(maintenance.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(maintenance.time, 'sleep', side_effect=sleep):
            with self.assertRaises(StopIteration):
                maintenance.main('files')
        jobs = [call.args[0] for call in invoke.call_args_list]
        self.assertEqual(jobs, [
            ['scan_fgtida_files', '--check-scanner'], ['scan_fgtida_files', '--limit', '20'],
            ['scan_fgtida_files', '--limit', '20', '--pending-only'],
            ['scan_fgtida_files', '--check-scanner'], ['scan_fgtida_files', '--limit', '20']])


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'runtime-fragment-tests'}})
class FragmentCacheTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_markdown_sanitization_is_reused_but_links_keep_their_own_context(self):
        from .templatetags.fgtida import rich_text
        from .rendering import markdown_fragment
        text = '[Read](guide.md)<script>alert(1)</script>'
        with patch('ietf.fgtida.rendering.markdown_fragment', wraps=markdown_fragment) as render:
            first = rich_text(text, 'https://first.example.test/docs/')
            self.assertEqual(rich_text(text, 'https://first.example.test/docs/'), first)
            self.assertEqual(render.call_count, 1)
            second = rich_text(text, 'https://second.example.test/docs/')
            self.assertEqual(render.call_count, 2)
        self.assertIn('https://first.example.test/docs/guide.md', first)
        self.assertIn('https://second.example.test/docs/guide.md', second)
        self.assertNotIn('<script', first)

    def test_rst_clean_cache_never_reuses_another_packages_image_permissions(self):
        from .templatetags.fgtida import rich_rst
        rendered = b'<p><img src="/approved.png"><a href="guide.md">Read</a></p>'
        with patch('subprocess.run', return_value=SimpleNamespace(stdout=rendered)) as parser:
            first = rich_rst('Example body', {'source_url': 'https://first.example.test/', 'image_urls': {'/approved.png'}})
            self.assertEqual(rich_rst('Example body', {'source_url': 'https://first.example.test/', 'image_urls': {'/approved.png'}}), first)
            second = rich_rst('Example body', {'source_url': 'https://second.example.test/', 'image_urls': set()})
            self.assertEqual(parser.call_count, 1)
        self.assertIn('src="/approved.png"', first)
        self.assertNotIn('<img', second)
        self.assertIn('https://second.example.test/guide.md', second)

    def test_image_validation_is_reused_only_for_identical_bytes_and_type(self):
        from .package_previews import image_response
        from .source_packages import pack
        with patch('ietf.fgtida.file_safety.inspect_file') as inspect:
            first = pack({'figure.png': b'first fixture image'})
            image_response(first, 'figure.png')
            image_response(first, 'figure.png')
            self.assertEqual(inspect.call_count, 1)
            image_response(pack({'figure.png': b'changed fixture image'}), 'figure.png')
            self.assertEqual(inspect.call_count, 2)


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False)
class ArchiveLoadTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def test_download_streams_bytes_and_rechecks_revoked_scan_verdict(self):
        data, scan = self.create_archives()
        with override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=True), patch('pathlib.Path.read_bytes', side_effect=AssertionError('Whole-file copy')):
            response = self.client.get('/doc/runtime-known-document/download/0.0/')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.streaming)
            self.assertEqual(b''.join(response.streaming_content), data)
            FileScan.objects.filter(pk=scan.pk).update(status='rejected', detail='Fixture rejected')
            self.assertEqual(self.client.get('/doc/runtime-known-document/download/0.0/').status_code, 403)

    def create_archives(self):
        data = b'Known immutable document.'
        doc = Document(name='runtime-known-document', title='Known document', type_id='tida', group=self.root)
        with override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=False):
            core.save_document(doc, core.actor_for(), 'Original', data=data, filename='original.txt')
        scan = FileScan.objects.create(sha256=hashlib.sha256(data).hexdigest(), filename='original.txt',
            size=len(data), status='clean', checked=timezone.now(), formats=['txt'])
        media = b'\x00\x00\x00\x18ftypmp42' + b'x'*128
        digest = hashlib.sha256(media).hexdigest()
        video = TrainingVideo.objects.create(title='Known training', filename='training.mp4', sha256=digest, file_size=len(media))
        TrainingVideoChunk.objects.create(resource=video, position=0, content=media)
        FileScan.objects.create(sha256=digest, filename=video.filename, size=len(media), status='admin', formats=['mp4'])
        return data, scan

    def test_recent_document_and_admin_video_do_not_reload_original_bytes(self):
        self.create_archives()
        with tempfile.TemporaryDirectory() as directory, override_settings(FGTIDA_QUARANTINE_DIR=directory, FGTIDA_UPLOAD_SCAN_REQUIRED=True), \
                patch('ietf.fgtida.file_safety.scan_stream') as antivirus, \
                patch('ietf.fgtida.management.commands.scan_fgtida_files.scanner_version', return_value='ClamAV test'), \
                patch('pathlib.Path.read_bytes', side_effect=AssertionError('Fresh original was reread')), CaptureQueriesContext(connection) as queries:
            call_command('scan_fgtida_files', limit=20, skip_checks=True, stdout=io.StringIO())
        antivirus.assert_not_called()
        self.assertFalse(any('fgtida_trainingvideochunk' in row['sql'].lower() for row in queries))

    def test_due_original_is_still_read_and_rescanned(self):
        data, scan = self.create_archives()
        FileScan.objects.filter(pk=scan.pk).update(checked=timezone.now()-timedelta(days=2))
        with tempfile.TemporaryDirectory() as directory, override_settings(FGTIDA_QUARANTINE_DIR=directory, FGTIDA_UPLOAD_SCAN_REQUIRED=True), \
                patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')) as antivirus, \
                patch('ietf.fgtida.file_safety.inspect_publication'), \
                patch('ietf.fgtida.management.commands.scan_fgtida_files.scanner_version', return_value='ClamAV test'):
            call_command('scan_fgtida_files', limit=20, skip_checks=True, stdout=io.StringIO())
        antivirus.assert_called_once_with(data)
        scan.refresh_from_db()
        self.assertEqual(scan.status, 'clean')
        self.assertGreater(scan.checked, timezone.now()-timedelta(minutes=1))

    def test_pending_only_never_traverses_existing_archives(self):
        self.create_archives()
        with tempfile.TemporaryDirectory() as directory, override_settings(FGTIDA_QUARANTINE_DIR=directory, FGTIDA_UPLOAD_SCAN_REQUIRED=True), \
                patch('ietf.fgtida.management.commands.scan_fgtida_files.scanner_version', return_value='ClamAV test'), \
                patch('pathlib.Path.read_bytes', side_effect=AssertionError('Archive read during pending-only cycle')), CaptureQueriesContext(connection) as queries:
            call_command('scan_fgtida_files', pending_only=True, skip_checks=True, stdout=io.StringIO())
        self.assertFalse(any('fgtida_documentversion' in row['sql'].lower() for row in queries))
