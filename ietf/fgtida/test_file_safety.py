"""Security boundaries for untrusted documents, quarantine and downloadable artifacts."""
import hashlib
import io
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import core
from .file_safety import allowed, inspect_file, scanner_version, validate_file
from .models import FileScan, DocumentFormat
from . import tests as native_tests


class ScannerProtocolTests(SimpleTestCase):
    def test_parser_environment_excludes_application_credentials(self):
        from .parser_process import environment
        with patch.dict('os.environ', {'FGTIDA_DB_PASSWORD': 'example-only', 'FGTIDA_SECRET_KEY': 'example-only',
                                      'RESEND_API_KEY': 'example-only', 'PATH': '/usr/bin'}):
            values = environment()
        self.assertEqual(values['PATH'], '/usr/bin')
        self.assertFalse(set(values).intersection({'FGTIDA_DB_PASSWORD', 'FGTIDA_SECRET_KEY', 'RESEND_API_KEY'}))
    @override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=True)
    def test_health_check_requires_detection_and_a_clean_control(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        output = io.StringIO()
        target = 'ietf.fgtida.management.commands.scan_fgtida_files.'
        with patch(target + 'scanner_version', return_value='ClamAV test'), patch(target + 'scan_stream') as scan:
            scan.side_effect = [('ClamAV test', 'Test.Pattern'), ('ClamAV test', '')]
            call_command('scan_fgtida_files', check_scanner=True, skip_checks=True, stdout=output)
            self.assertIn('detection and clean-control tests passed', output.getvalue())
            self.assertEqual(hashlib.md5(scan.call_args_list[0].args[0]).hexdigest(), '44d88612fea8a8f36de82e1278abb02f')
            scan.side_effect = [('ClamAV test', ''), ('ClamAV test', '')]
            with self.assertRaises(CommandError):
                call_command('scan_fgtida_files', check_scanner=True, skip_checks=True, stdout=io.StringIO())
    def test_version_rejects_stale_definitions_and_unrecognized_protocol(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        with patch('ietf.fgtida.file_safety.scanner_connection', return_value=connection), patch('ietf.fgtida.file_safety.reply') as reply:
            reply.return_value = 'ClamAV 1.4.3/12345/' + timezone.now().strftime('%a %b %d %H:%M:%S %Y')
            self.assertTrue(scanner_version().startswith('ClamAV '))
            reply.return_value = 'ClamAV 1.4.3/12345/Mon Jan 01 00:00:00 2001'
            with self.assertRaises(OSError):
                scanner_version()
            reply.return_value = 'OK'
            with self.assertRaises(OSError):
                scanner_version()

    @override_settings(FGTIDA_CLAMAV_HOST='public-scanner.example.test', FGTIDA_CLAMAV_PORT=3310)
    def test_private_documents_cannot_be_sent_to_a_public_scanner(self):
        from .file_safety import scanner_connection
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 3310))]), patch('socket.create_connection') as connect:
            with self.assertRaises(OSError):
                scanner_connection()
            connect.assert_not_called()

    def test_structural_validator_rejects_active_pdf_and_macro_spoof(self):
        from pypdf import PdfWriter
        from .source_packages import pack
        pdf = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_js('app.alert("unsafe");')
        writer.write(pdf)
        with self.assertRaises(ValidationError):
            inspect_file(pdf.getvalue(), 'pdf')
        types = b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
        word = pack({'[Content_Types].xml': types, 'word/document.xml': b'<document/>', 'word/vbaProject.bin': b'fake macro'})
        with self.assertRaises(ValidationError):
            inspect_file(word, 'docx')
        passive = {'[Content_Types].xml': types, 'word/document.xml': b'<document/>'}
        inspect_file(pack(passive), 'docx')
        dde = {**passive, 'word/document.xml': b'<document><instrText>DDE</instrText><instrText>AUTO example</instrText></document>'}
        with self.assertRaises(ValidationError):
            inspect_file(pack(dde), 'docx')
        remote = {**passive, 'word/_rels/document.xml.rels': b'<Relationships><Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" TargetMode="External" Target="file:///private"/></Relationships>'}
        with self.assertRaises(ValidationError):
            inspect_file(pack(remote), 'docx')
        with self.assertRaises(ValidationError):
            inspect_file(pack({'index.rst': b'Example\n=======', 'active.pdf': pdf.getvalue()}), 'zip')


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False)
class FileSafetyTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp
    create_doc = native_tests.NativeDatatrackerTests.create_doc
    admin_payload = native_tests.NativeDatatrackerTests.admin_payload

    def test_repeated_submission_attempts_stop_before_file_parsing(self):
        from .mfa import allow_attempt
        for _ in range(20):
            self.assertTrue(allow_attempt(f'file-submission:{self.user.pk}', limit=20))
        with patch('ietf.fgtida.contributions.ContributionForm') as form:
            response = self.client.post('/submit/', {'title': 'Repeated attempt'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '900')
        form.assert_not_called()

    def test_scanner_outage_keeps_original_private_and_requires_resubmission(self):
        data = b'Private source awaiting scanning'
        with tempfile.TemporaryDirectory() as root, override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=True, FGTIDA_QUARANTINE_DIR=root):
            with patch('ietf.fgtida.file_safety.scan_stream', side_effect=OSError):
                with self.assertRaises(ValidationError):
                    validate_file(data, 'source.txt')
            digest = hashlib.sha256(data).hexdigest()
            record = FileScan.objects.get(pk=digest)
            self.assertEqual(record.status, 'error')
            self.assertEqual((Path(root) / digest).read_bytes(), data)
            self.assertFalse(allowed(data, 'source.txt'))
            with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')):
                validate_file(data, 'source.txt')
            self.assertTrue(allowed(data, 'source.txt'))
            self.assertFalse((Path(root) / digest).exists())

    def test_detection_rejects_files_and_a_bad_extension_cannot_revoke_a_clean_file(self):
        data = b'Example text document'
        with tempfile.TemporaryDirectory() as root, override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=True, FGTIDA_QUARANTINE_DIR=root):
            with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', 'Example.Detection')), patch('ietf.fgtida.file_safety.inspect_file') as inspect:
                with self.assertRaises(ValidationError):
                    validate_file(data, 'source.txt')
                inspect.assert_not_called()
            self.assertEqual(FileScan.objects.get(pk=hashlib.sha256(data).hexdigest()).status, 'rejected')
            self.assertFalse(allowed(data, 'source.txt'))
            with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')):
                validate_file(data, 'source.txt', force=True)
            with self.assertRaises(ValidationError):
                validate_file(data, 'spoof.pdf')
            self.assertTrue(allowed(data, 'source.txt'))
            self.assertFalse(allowed(data, 'spoof.pdf'))

    def test_public_preview_original_alternatives_text_and_tar_wait_for_scan(self):
        document = self.create_doc()
        from ietf.meeting.models import Meeting, Session, SessionPresentation
        meeting = Meeting.objects.create(number='scan-protected-session', type_id='tida', date=timezone.localdate(), days=1, time_zone='UTC')
        session = Session.objects.create(meeting=meeting, group=self.root, name='Meeting session', type_id='session', purpose_id='regular')
        SessionPresentation.objects.create(session=session, document=document)
        raw = Path(document.get_file_name()).read_bytes()
        alternative = DocumentFormat.objects.create(document=document, revision=document.rev, kind='docx',
            filename='original.docx', content=b'Unsafe original bytes')
        from .versioning import public_version
        version = public_version(document)
        self.client.logout()
        with tempfile.TemporaryDirectory() as root, override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=True, FGTIDA_QUARANTINE_DIR=root):
            detail = self.client.get(f'/doc/{document.name}/')
            self.assertContains(detail, 'awaiting security checks')
            self.assertNotContains(detail, 'No file uploaded')
            self.assertContains(detail, 'File awaiting security checks or review')
            for path in [f'/doc/{document.name}/download/{version}/', f'/doc/{document.name}/text/{version}/',
                         f'/document-formats/{alternative.pk}/download/', f'/session/{session.pk}/materials.tar']:
                self.assertEqual(self.client.get(path).status_code, 403)
            with patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')):
                validate_file(raw, document.uploaded_filename)
            self.assertEqual(self.client.get(f'/doc/{document.name}/download/{version}/').status_code, 200)
            self.assertEqual(self.client.get(f'/doc/{document.name}/text/{version}/').status_code, 200)
            self.assertEqual(self.client.get(f'/session/{session.pk}/materials.tar').status_code, 200)
            self.assertNotContains(self.client.get(f'/doc/{document.name}/'), 'awaiting security checks')

    @override_settings(FGTIDA_MAIL_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_contribution_emails_never_attach_word_or_archives(self):
        from django.core import mail
        from .mail import enqueue, deliver
        from .models import Contribution
        item = Contribution.objects.create(title='Example', group=self.root, submitter=self.user,
            filename='original.docx', content=b'Untrusted document')
        message = enqueue(self.user.email, 'Receipt', 'Read the contribution on Datatracker.', contribution=item, purpose='submitter')
        deliver(message.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].attachments, [])
