import hashlib
import io
import tempfile
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import tests as native_tests
from .file_safety import allowed, quarantine_path, reply, scan_stream, scanner_test_pattern, scanner_version, validate_file
from .models import FileScan


class ScannerTrustTests(SimpleTestCase):
    def test_maintenance_skips_file_processing_if_the_health_test_fails(self):
        from deploy import maintenance
        with patch.object(maintenance, 'invoke', return_value=False) as invoke, \
                patch.object(maintenance.time, 'sleep', side_effect=[None, StopIteration]), \
                patch('builtins.print'):
            with self.assertRaises(StopIteration):
                maintenance.main('files')
        invoke.assert_called_once_with(['scan_fgtida_files', '--check-scanner'], 180)

    def test_each_scan_proves_detection_and_clean_control_first(self):
        with patch('ietf.fgtida.file_safety.scanner_version', return_value='ClamAV test'), \
                patch('ietf.fgtida.file_safety._scan_stream', side_effect=['Win.Test.EICAR', '', '']) as raw:
            self.assertEqual(scan_stream(b'actual upload'), ('ClamAV test', ''))
            self.assertEqual(raw.call_args_list[0].args[0], scanner_test_pattern())
            self.assertEqual(raw.call_args_list[-1].args[0], b'actual upload')
            self.assertEqual(raw.call_count, 3)
        for outcomes in [['', ''], ['Win.Test.EICAR', 'Unexpected.Detection']]:
            with patch('ietf.fgtida.file_safety.scanner_version', return_value='ClamAV test'), \
                    patch('ietf.fgtida.file_safety._scan_stream', side_effect=outcomes) as raw:
                with self.assertRaises(OSError):
                    scan_stream(b'actual upload')
                self.assertNotIn(b'actual upload', [call.args[0] for call in raw.call_args_list])

    def test_incomplete_or_oversized_verdict_cannot_approve_a_file(self):
        for payload in [b'stream: OK', b'x' * 4096]:
            connection = Mock()
            connection.recv.side_effect = [bytes([value]) for value in payload] + [b'']
            with self.assertRaises(OSError):
                reply(connection)
        connection.recv.side_effect = [bytes([value]) for value in b'stream: OK\0']
        self.assertEqual(reply(connection), 'stream: OK')

    def test_definitions_older_than_three_days_are_rejected(self):
        from datetime import timedelta
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        stamp = (timezone.now() - timedelta(days=4)).strftime('%a %b %d %H:%M:%S %Y')
        with patch('ietf.fgtida.file_safety.scanner_connection', return_value=connection), \
                patch('ietf.fgtida.file_safety.reply', return_value='ClamAV 1.4.6/123/' + stamp):
            with self.assertRaises(OSError):
                scanner_version()


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=True)
class ScannerRecoveryTests(TestCase):
    def setUp(self):
        native_tests.NativeDatatrackerTests.setUp(self)
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        config = override_settings(FGTIDA_QUARANTINE_DIR=root.name)
        config.enable()
        self.addCleanup(config.disable)

    def rejected(self, data=b'Example document for review.', filename='example.txt'):
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', 'Example.Detection')):
            with self.assertRaises(ValidationError):
                validate_file(data, filename)
        return FileScan.objects.get(pk=hashlib.sha256(data).hexdigest())

    def test_review_requires_reason_and_preserves_history_without_override(self):
        data = b'Example document for review.'
        record = self.rejected(data)
        self.assertEqual(quarantine_path(record.pk).read_bytes(), data)
        self.assertFalse(allowed(data, record.filename))
        url = '/admin/fgtida/filescan/'
        payload = {'action': 'retry_checks', '_selected_action': record.pk}
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV updated', '')) as scan:
            response = self.client.post(url, payload)
            self.assertContains(response, 'Review and retry file security checks')
            scan.assert_not_called()
            response = self.client.post(url, {**payload, 'confirm_scan_retry': 'yes', 'reason': 'short'})
            self.assertContains(response, 'at least 10 characters')
            scan.assert_not_called()
            response = self.client.post(url, {**payload, 'confirm_scan_retry': 'yes',
                'reason': 'Signature updated after investigation.'})
            self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, 'clean')
        self.assertTrue(allowed(data, record.filename))
        self.assertFalse(quarantine_path(record.pk).exists())
        self.assertEqual(list(record.events.order_by('pk').values_list('operation', 'status')),
                         [('scan', 'rejected'), ('review', 'rejected'), ('scan', 'clean')])
        self.assertEqual(record.events.get(operation='review').actor_id, self.user.pk)
        self.assertEqual(record.events.order_by('pk').first().detail, 'Example.Detection')

    def test_review_never_bypasses_structural_rejection(self):
        data = b'This is not a PDF.'
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')):
            for _ in range(2):
                with self.assertRaises(ValidationError):
                    validate_file(data, 'bad.pdf', force=True)
        record = FileScan.objects.get(pk=hashlib.sha256(data).hexdigest())
        self.assertEqual(record.status, 'blocked')
        self.assertFalse(allowed(data, 'bad.pdf'))
        self.assertEqual(record.events.count(), 2)

    def test_targeted_recovery_cannot_release_another_file(self):
        data = b'Example document for review.'
        selected = self.rejected(data)
        other = self.rejected(b'A different document.')
        target = 'ietf.fgtida.management.commands.scan_fgtida_files.'
        with patch(target + 'scanner_version', return_value='ClamAV updated'), \
                patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV updated', '')):
            with self.assertRaises(CommandError):
                call_command('scan_fgtida_files', sha256=selected.pk, force=True, skip_checks=True, stdout=io.StringIO())
            call_command('scan_fgtida_files', sha256=selected.pk, force=True,
                reason='Operator verified updated signatures.', skip_checks=True, stdout=io.StringIO())
        selected.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(selected.status, 'clean')
        self.assertEqual(other.status, 'rejected')
        self.assertEqual(selected.events.filter(operation='review').count(), 1)

    def test_expiry_removes_bytes_and_preserves_the_rejection_audit(self):
        import os
        import time
        record = self.rejected()
        os.utime(quarantine_path(record.pk), (time.time() - 8 * 86400,) * 2)
        from .management.commands.scan_fgtida_files import Command
        Command().expire_private_uploads()
        self.assertFalse(quarantine_path(record.pk).exists())
        record.refresh_from_db()
        self.assertEqual(record.status, 'rejected')
        self.assertTrue(record.events.filter(operation='expired').exists())

    def test_staff_cannot_access_or_retry_scanner_decisions(self):
        from django.contrib.auth import get_user_model
        record = self.rejected()
        staff = get_user_model().objects.create_user('review-staff', is_staff=True)
        self.client.force_login(staff)
        response = self.client.post('/admin/fgtida/filescan/', {'action': 'retry_checks',
            '_selected_action': record.pk, 'confirm_scan_retry': 'yes', 'reason': 'Attempt without superuser rights.'})
        self.assertEqual(response.status_code, 403)
        record.refresh_from_db()
        self.assertEqual(record.status, 'rejected')
