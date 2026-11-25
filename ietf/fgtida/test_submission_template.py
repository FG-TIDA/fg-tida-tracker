import subprocess
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from . import core, tests as native_tests
from .models import Contribution, OutgoingMail
from .source_packages import pack, unpack, convert_word
from .submission_template import check_archive, problems
from .template_repairs import fit_text, prepare, apply_fit


def completed_source(body='Technical requirements and supporting evidence.'):
    return ('A specification\n===============\n\n'
        'Abstract\n--------\n\nA description of the proposed work.\n\n'
        'History\n-------\n\nVersion: 0.3\nDocument: A specification\nApproval: Not approved\nFocus Group: TIDA\nWorking Group: Incubation\n\n'
        'Authors\n-------\n\nAuthor: Example Expert\nAffiliation: Example Institute\nContact: expert@example.test\n\n'
        'Keywords\n--------\n\nIdentity, trust\n\n'
        'Foreword\n--------\n\nThis is an individual contribution to FG-TIDA.\n\n'
        'Requirements\n------------\n\n' + body + '\n').encode()


class TemplateValidationTests(SimpleTestCase):
    def test_complete_front_matter_and_word_round_trip(self):
        source = completed_source()
        self.assertEqual(problems(source.decode()), [])
        word = subprocess.run(['pandoc', '-f', 'rst', '-t', 'docx'], input=source,
                              capture_output=True, check=True).stdout
        archive, manifest = convert_word(word)
        self.assertEqual(check_archive(archive, manifest, word=True)['template_validation']['entry_document'],
                         'chapters/01_contribution.rst')
        # A generated Word ZIP can be edited and resubmitted as an RST package.
        check_archive(archive, {'entry_document': 'index.rst'})

    def test_missing_order_duplicate_placeholder_and_logo_errors(self):
        valid = completed_source().decode()
        cases = [
            (valid.replace('Authors\n-------', 'Credits\n-------'), 'Authors'),
            (valid.replace('Version: 0.3', 'Version: draft'), 'X.Y'),
            (valid.replace('Identity, trust', 'Lorem, ipsum'), 'Keywords'),
            (valid + '\nAuthors\n-------\n\nSomeone else\n', 'one Authors'),
            ('.. image:: images/logo.png\n\n' + valid, 'logos'),
            (valid.replace('Abstract\n--------', 'Keywords\n--------').replace('Keywords\n--------\n\nIdentity', 'Abstract\n--------\n\nIdentity'), 'order'),
        ]
        for source, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, ' '.join(problems(source)))

    def test_unedited_optional_chapter_is_rejected(self):
        archive = pack({'index.rst': completed_source(), 'chapters/example.rst': b'Lorem ipsum example.'})
        with self.assertRaisesMessage(ValidationError, 'throughout'):
            check_archive(archive, {'entry_document': 'index.rst'})

    def test_word_wrapper_cannot_disguise_incomplete_contribution(self):
        archive = pack({'index.rst': completed_source(), 'chapters/01_contribution.rst': b'Incomplete body'})
        with self.assertRaisesMessage(ValidationError, 'Abstract'):
            check_archive(archive, {'entry_document': 'index.rst'}, word=True)

    def test_adaptation_preserves_body_and_moves_legacy_cover(self):
        source = '.. image:: images/logo.png\n\n' + completed_source('A table and a diagram.').decode()
        fitted = fit_text(source, title='A specification', version='0.4')
        self.assertTrue(fitted.startswith('A specification\n'))
        self.assertIn('A table and a diagram.', fitted)
        self.assertGreater(fitted.index('.. image:: images/logo.png'), fitted.index('Requirements'))
        self.assertEqual(problems(fitted), [])
        incomplete = fit_text('Old text', title='Legacy', version='0.1')
        self.assertIn('administrator review required', incomplete)
        self.assertTrue(problems(incomplete))


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=False,
                   FGTIDA_MAIL_ENABLED=False, FGTIDA_MAIL_IMMEDIATE=False)
class TemplateSubmissionTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def payload(self, source):
        from ietf.group.models import Group
        return {'title': 'A specification', 'group': Group.objects.get(acronym='tidawg1').pk,
            'meeting': native_tests.open_submission_meeting().pk, 'submission_method': 'rst',
            'entry_document': 'index.rst', 'publication_consent': 'on',
            'upload': SimpleUploadedFile('source.zip', pack({'index.rst': source}))}

    def test_incomplete_upload_creates_no_contribution_document_or_mail(self):
        from ietf.doc.models import Document
        before = Document.objects.count()
        response = self.client.post('/submit/', self.payload(b'Unstructured proposal'))
        self.assertContains(response, 'Add the Abstract heading')
        self.assertFalse(Contribution.objects.exists())
        self.assertFalse(OutgoingMail.objects.exists())
        self.assertEqual(Document.objects.count(), before)

    def test_incomplete_word_is_rejected_before_publication(self):
        payload = self.payload(completed_source())
        word = subprocess.run(['pandoc', '-f', 'rst', '-t', 'docx'], input=b'Unstructured Word proposal',
                              capture_output=True, check=True).stdout
        payload.update(submission_method='word', entry_document='', upload=SimpleUploadedFile('source.docx', word))
        response = self.client.post('/submit/', payload)
        self.assertContains(response, 'Add the Abstract heading')
        self.assertFalse(Contribution.objects.exists())
        self.assertFalse(OutgoingMail.objects.exists())

    def test_valid_submission_and_incomplete_github_revision(self):
        self.assertEqual(self.client.post('/submit/', self.payload(completed_source())).status_code, 302)
        item = Contribution.objects.get()
        self.assertIn('template_validation', item.source_manifest)
        from .contributions import archive_github_revision
        with patch('ietf.fgtida.source_packages.github_snapshot', return_value=(pack({'index.rst': b'Bad revision'}), {'entry_document': 'index.rst'})):
            with self.assertRaisesMessage(ValidationError, 'Abstract'):
                archive_github_revision(item.pk, self.user, 'https://github.com/example/spec', 'a'*40,
                                        'src/doc', 'src/doc/index.rst', '0.4')

    def test_existing_fit_preserves_assets_original_and_history_without_mail(self):
        from ietf.doc.models import Document
        original = pack({'index.rst': b'.. image:: images/logo.svg\n\n' + completed_source(),
                         'images/logo.svg': b'<svg xmlns="http://www.w3.org/2000/svg"/>'})
        doc = Document.objects.create(name='legacy-template-test', title='A specification', type_id='tida', group=self.root)
        core.save_document(doc, core.actor_for(self.user), 'Original source.', data=original, filename='source.zip', version='0.3')
        item = Contribution.objects.create(submitter=self.user, group=self.root, title=doc.title,
            document=doc, content=original, filename='original.zip', source_archive=original,
            source_manifest={'entry_document': 'index.rst'})
        report, adapted, previous = prepare(item)
        self.assertEqual(report['state'], 'ready')
        self.assertEqual(unpack(adapted)['images/logo.svg'], unpack(original)['images/logo.svg'])
        with patch('ietf.fgtida.file_safety.allowed', return_value=False):
            with self.assertRaisesMessage(ValidationError, 'safety status changed'):
                apply_fit(item.pk, report, adapted, previous, 'Do not adapt a newly blocked source.')
        apply_fit(item.pk, report, adapted, previous, 'Normalize the template front matter, retaining the original.')
        item.refresh_from_db()
        self.assertEqual(bytes(item.content), original)
        self.assertEqual(item.document.tida_versions.get(revision=item.document.rev).version, '0.4')
        self.assertTrue(item.document.tida_versions.filter(version='0.3').exists())
        from .versioning import declared_version
        self.assertEqual(declared_version(unpack(bytes(item.source_archive))['index.rst'].decode()), '0.4')
        self.assertFalse(OutgoingMail.objects.exists())
        again, _, _ = prepare(Contribution.objects.select_related('document').get(pk=item.pk))
        self.assertEqual(again['state'], 'awaiting_author_review')
        page = self.client.get(f'/doc/{doc.name}/')
        self.assertNotContains(page, 'Editorial template adaptation — author verification pending.')
        self.assertNotContains(page, 'not fully template-compliant')

    def test_catalogue_sources_are_not_overwritten(self):
        item = Contribution.objects.create(submitter=self.user, group=self.root, title='Catalogue', status='catalogued')
        with self.assertRaises(ValidationError):
            prepare(item)
        output = StringIO()
        call_command('fit_fgtida_templates', contribution=item.pk, stdout=output)
        self.assertIn('source_managed', output.getvalue())

    def test_missing_information_cannot_be_applied(self):
        source = completed_source().decode().replace('Identity, trust', 'Not supplied — administrator review required.')
        fitted = fit_text(source, title='A specification', version='0.4')
        self.assertTrue(problems(fitted))
        with self.assertRaisesMessage(ValidationError, 'Only a complete'):
            apply_fit(0, {'state': 'needs_review'}, b'', b'', 'Review missing keywords before publication.')

    def test_provisional_fit_and_private_submitter_report_are_repeatable(self):
        import csv
        import tempfile
        from pathlib import Path
        from ietf.doc.models import Document
        from .management.commands.fit_fgtida_templates import spreadsheet_cell
        original = pack({'index.rst': b'Old proposal\n============\n\nOriginal technical content.\n'})
        doc = Document.objects.create(name='legacy-needs-review', title='Existing proposal', type_id='tida', group=self.root)
        core.save_document(doc, core.actor_for(self.user), 'Original file.', data=original, filename='source.zip', version='0.0')
        self.user.first_name, self.user.last_name, self.user.email = 'Actual', 'Submitter', 'submitter@example.test'
        self.user.save()
        item = Contribution.objects.create(submitter=self.user, group=self.root, title=doc.title,
            document=doc, content=original, filename='original.zip', source_archive=original,
            source_manifest={'entry_document': 'index.rst'}, contact_name='Different Submission Contact', contact_email='contact@example.test')
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'private.csv'
            output = StringIO()
            call_command('fit_fgtida_templates', contribution=item.pk, apply=True, allow_incomplete_legacy=True,
                         reason='Editorial formatting only; authors will verify their adapted source.', contacts_csv=str(report), stdout=output)
            self.assertIn('archived_provisional', output.getvalue())
            self.assertNotIn('submitter@example.test', output.getvalue())
            self.assertNotIn('contact@example.test', output.getvalue())
            with report.open(encoding='utf-8-sig', newline='') as source:
                row = list(csv.DictReader(source))[0]
            self.assertEqual(row['submitter_name'], 'Actual Submitter')
            self.assertEqual(row['submitter_email'], 'submitter@example.test')
            self.assertEqual(row['submission_contact_email'], 'contact@example.test')
            self.assertTrue(row['issues'])
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(spreadsheet_cell(' =HYPERLINK("bad")'), '\' =HYPERLINK("bad")')
        item = Contribution.objects.select_related('document').get(pk=item.pk)
        self.assertEqual(bytes(item.content), original)
        self.assertIn(b'Original technical content.', unpack(bytes(item.source_archive))['index.rst'])
        revision = item.document.rev
        record = item.document.tida_versions.get(revision=revision)
        self.assertTrue(record.source_metadata['template_adaptation']['provisional'])
        self.assertEqual(record.version, '0.1')
        self.assertContains(self.client.get(f'/doc/{doc.name}/'), 'author verification pending')
        self.assertContains(self.client.get(f'/doc/{doc.name}/'), 'not fully template-compliant')
        again = StringIO()
        call_command('fit_fgtida_templates', contribution=item.pk, apply=True, allow_incomplete_legacy=True,
                     reason='Repeat should not create another version.', stdout=again)
        self.assertIn('awaiting_author_review', again.getvalue())
        item.document.refresh_from_db()
        self.assertEqual(item.document.rev, revision)
        self.assertFalse(OutgoingMail.objects.exists())
        with self.assertRaises(ValidationError):
            check_archive(bytes(item.source_archive), {'entry_document': 'index.rst'})
