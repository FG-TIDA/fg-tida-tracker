import hashlib
import io
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from . import tests as native_tests
from .file_safety import allowed, approve_false_positive, quarantine_path, validate_file
from .models import FileScan


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=True)
class ScanExceptionTests(TestCase):
    create_doc = native_tests.NativeDatatrackerTests.create_doc
    admin_payload = native_tests.NativeDatatrackerTests.admin_payload

    def setUp(self):
        native_tests.NativeDatatrackerTests.setUp(self)
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        config = override_settings(FGTIDA_QUARANTINE_DIR=root.name)
        config.enable()
        self.addCleanup(config.disable)
        self.data = b'An original document independently reviewed by the administrator.'
        self.reason = 'Reviewed the original independently; the detected pattern is legitimate document text.'

    def reject(self, data=None, filename='review.txt', finding='Example.False.Positive'):
        data = self.data if data is None else data
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', finding)):
            with self.assertRaises(ValidationError):
                validate_file(data, filename, force=True)
        return FileScan.objects.get(pk=hashlib.sha256(data).hexdigest())

    def approve(self, record):
        finding = record.detail
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', finding)):
            return approve_false_positive(record.pk, self.user, self.reason, expected_detection=finding)

    def exception_payload(self, record):
        payload = {'action': 'approve_exception', '_selected_action': record.pk}
        response = self.client.post('/admin/fgtida/filescan/', payload)
        self.assertContains(response, 'Review one antivirus false positive')
        return {**payload, 'confirm_exception': 'yes', 'reason': self.reason,
            'review_token': response.context['form']['review_token'].value(),
            'password': 'test-password', 'accept_risk': 'on'}

    def test_admin_can_grant_and_revoke_an_exact_exception_with_audit(self):
        record = self.reject()
        payload = self.exception_payload(record)
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', record.detail)):
            response = self.client.post('/admin/fgtida/filescan/', payload)
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, 'rejected')  # The original verdict is never called clean.
        self.assertEqual(record.detail, 'Example.False.Positive')
        self.assertTrue(allowed(self.data, record.filename))
        self.assertFalse(allowed(self.data + b' edited', record.filename))
        self.assertFalse(allowed(self.data, 'different.pdf'))
        event = record.events.get(operation='exception')
        self.assertEqual(event.actor_id, self.user.pk)
        self.assertEqual(event.detail, self.reason)
        with patch('ietf.fgtida.file_safety.scan_stream') as scan:
            validate_file(self.data, record.filename)
            scan.assert_not_called()
        self.client.post('/admin/fgtida/filescan/', {'action': 'revoke_exception', '_selected_action': record.pk})
        self.assertFalse(allowed(self.data, record.filename))
        self.assertTrue(record.events.filter(operation='revoke', actor=self.user).exists())

    def test_public_download_obeys_exception_and_revocation(self):
        from pathlib import Path
        from .file_safety import revoke_exceptions
        with override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=False):
            doc = self.create_doc()
        data = Path(doc.get_file_name()).read_bytes()
        record = self.reject(data, doc.uploaded_filename)
        url = f'/doc/{doc.name}/download/{doc.rev}/'
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.approve(record)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), data)
        revoke_exceptions(record, actor=self.user)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_password_confirmation_reason_and_current_finding_are_required(self):
        record = self.reject()
        payload = self.exception_payload(record)
        with patch('ietf.fgtida.file_safety.scan_stream') as scan:
            for change in [{'password': 'wrong'}, {'reason': 'short'}, {'accept_risk': ''}, {'review_token': 'tampered'}]:
                response = self.client.post('/admin/fgtida/filescan/', {**payload, **change})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(allowed(self.data, record.filename))
                self.assertNotContains(response, 'value="wrong"')
            FileScan.objects.filter(pk=record.pk).update(detail='Changed.Detection')
            response = self.client.post('/admin/fgtida/filescan/', payload)
            self.assertContains(response, 'finding changed')
            scan.assert_not_called()

    def test_unsupported_media_never_receive_an_exception(self):
        record = self.reject(b'Not a genuine SVG.', 'invalid.svg')
        with self.assertRaisesMessage(ValidationError, 'Publication limits failed'):
            self.approve(record)
        self.assertFalse(record.exceptions.exists())
        self.assertFalse(allowed(b'Not a genuine SVG.', 'invalid.svg'))
        self.assertTrue(record.events.filter(operation='denied').exists())

    def test_staff_and_scanner_errors_cannot_be_overridden(self):
        record = self.reject()
        staff = get_user_model().objects.create_user('exception-staff', is_staff=True)
        with self.assertRaises(PermissionDenied):
            approve_false_positive(record.pk, staff, self.reason, expected_detection=record.detail)
        self.client.force_login(staff)
        self.assertEqual(self.client.post('/admin/fgtida/filescan/', {'action': 'approve_exception',
            '_selected_action': record.pk}).status_code, 403)
        with patch('ietf.fgtida.file_safety.scan_stream', side_effect=OSError):
            with self.assertRaises(ValidationError):
                approve_false_positive(record.pk, self.user, self.reason, expected_detection=record.detail)
        self.assertFalse(record.exceptions.exists())

    def test_limits_and_changed_detections_cannot_receive_the_old_exception(self):
        record = self.reject(finding='Heuristics.Limits.Exceeded.MaxScanSize')
        with self.assertRaisesMessage(ValidationError, 'cannot be overridden'):
            self.approve(record)
        record = self.reject()
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', 'New.Detection')):
            with self.assertRaises(ValidationError):
                approve_false_positive(record.pk, self.user, self.reason, expected_detection=record.detail)
        self.assertFalse(record.exceptions.exists())

    def test_failed_fresh_check_cannot_approve_an_old_verdict(self):
        record = self.reject()
        with patch('ietf.fgtida.file_safety.validate_file', side_effect=ValidationError('Queue unavailable')):
            with self.assertRaisesMessage(ValidationError, 'fresh scanner check did not complete'):
                approve_false_positive(record.pk, self.user, self.reason, expected_detection=record.detail)
        self.assertFalse(record.exceptions.exists())

    def test_periodic_scan_keeps_same_exception_and_revokes_for_a_new_finding(self):
        record = self.reject()
        self.approve(record)
        FileScan.objects.filter(pk=record.pk).update(checked=timezone.now() - timedelta(days=2))
        from .management.commands.scan_fgtida_files import Command
        command = Command(stdout=io.StringIO())
        command.target, command.force, command.review_recorded = None, False, False
        command.processed = set()
        command.attempted = command.failed = command.passed = 0
        command.limit = 20
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', record.detail)) as scan:
            self.assertTrue(command.check(self.data, record.filename))
            scan.assert_called_once()
        self.assertTrue(allowed(self.data, record.filename))
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', 'Different.Detection')):
            with self.assertRaises(ValidationError):
                validate_file(self.data, record.filename, force=True)
        self.assertFalse(allowed(self.data, record.filename))
        self.assertFalse(record.exceptions.filter(revoked_at__isnull=True).exists())

    def test_updated_publication_limits_can_revoke_an_existing_exception(self):
        record = self.reject()
        self.approve(record)
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', record.detail)), \
                patch('ietf.fgtida.file_safety.inspect_publication', side_effect=ValidationError('Archive limit exceeded')):
            with self.assertRaises(ValidationError):
                validate_file(self.data, record.filename, force=True)
        self.assertFalse(allowed(self.data, record.filename))
        self.assertFalse(record.exceptions.filter(revoked_at__isnull=True).exists())

    def test_clean_rescan_needs_no_exception_and_expiry_preserves_the_finding(self):
        import os
        import time
        record = self.reject()
        self.approve(record)
        os.utime(quarantine_path(record.pk), (time.time() - 8 * 86400,) * 2)
        from .management.commands.scan_fgtida_files import Command
        Command().expire_private_uploads()
        record.refresh_from_db()
        self.assertEqual(record.detail, 'Example.False.Positive')
        self.assertTrue(allowed(self.data, record.filename))
        # A true clean rescan replaces the exception, without an earlier waiver lingering.
        with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV updated', '')):
            validate_file(self.data, record.filename, force=True)
        record.refresh_from_db()
        self.assertEqual(record.status, 'clean')
        self.assertFalse(record.exceptions.filter(revoked_at__isnull=True).exists())
