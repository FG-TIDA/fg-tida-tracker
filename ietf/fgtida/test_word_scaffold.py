import hashlib
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from ietf.doc.models import Document
from . import core, tests as native_tests, test_admin_contributions as admin_tests
from .contributions import convert_existing_word
from .models import Contribution
from .source_packages import pack, unpack, validate_sources
from .word_scaffold import PROFILE, wrap_word_source


class WordScaffoldTests(SimpleTestCase):
    def test_scaffold_contains_runtime_assets_and_only_actual_contribution_content(self):
        files = wrap_word_source('A proposal\n==========\n\n**Version:** 0.3\n\nReal contribution text.\n\n.. image:: media/figure.png\n',
                                 {'media/figure.png': b'image preserved'})
        for name in ['index.rst', 'chapters/01_contribution.rst', 'conf.py', '_static/osia-furo.css',
                     '_static/osia-sidebar.js', '_templates/page.html', 'images/TIDA_logo.png',
                     'images/word/figure.png', 'requirements.txt', 'LICENSE.txt', 'conversion.json']:
            self.assertIn(name, files)
        self.assertIn(b'../images/word/figure.png', files['chapters/01_contribution.rst'])
        self.assertIn(b"release = '0.3'", files['conf.py'])
        self.assertIn(b'Real contribution text.', files['chapters/01_contribution.rst'])
        self.assertNotIn('chapters/01_chapter1.rst', files)
        self.assertEqual(unpack(pack(files)), files)

    def test_reviewed_script_and_layout_are_allowed_but_modifications_are_rejected(self):
        files = wrap_word_source('Proposal\n========\n\nBody.', {})
        validate_sources(files, 'index.rst')
        for name in ['_static/osia-sidebar.js', '_templates/page.html']:
            changed = {**files, name: files[name] + b' unreviewed modification'}
            with self.assertRaises(ValidationError):
                validate_sources(changed, 'index.rst')
        with self.assertRaises(ValidationError):
            validate_sources({**files, 'extra.js': b'alert(1)'}, 'index.rst')


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=True,
                   FGTIDA_MAIL_ENABLED=False, FGTIDA_MAIL_IMMEDIATE=False)
class WordPackageRebuildTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp
    word = admin_tests.AdminContributionTests.word

    def old_conversion(self):
        word = self.word()
        slim = pack({'index.rst': b'Original proposal\n=================\n\nA preserved paragraph.'})
        doc = Document(name='older-word-package', title='Original proposal', type_id='tida', group=self.root)
        with override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=False):
            core.save_document(doc, core.actor_for(self.user), 'Earlier Word conversion.', data=slim, filename='source.zip')
        item = Contribution.objects.create(title=doc.title, document=doc, group=self.root, submitter=self.user,
            filename='original.docx', content=word, source_archive=slim, submission_method='word',
            source_manifest={'sha256': hashlib.sha256(slim).hexdigest(), 'entry_document': 'index.rst'},
            conversion_verified_at=timezone.now(), conversion_verified_by=self.user)
        return item, slim

    def test_rebuild_is_explicit_preserves_prior_archive_and_resets_verification(self):
        item, slim = self.old_conversion()
        revision = item.document.rev
        original_path = Path(item.document.get_file_name())
        reference, document_id = item.reference, item.document_id
        payload = {'action': 'rebuild_word_packages', '_selected_action': item.pk}
        page = self.client.post('/admin/fgtida/contribution/', payload)
        self.assertContains(page, 'Rebuild Word source packages')
        item.refresh_from_db()
        self.assertEqual(bytes(item.source_archive), slim)
        with tempfile.TemporaryDirectory() as root, override_settings(FGTIDA_QUARANTINE_DIR=root), \
                patch('ietf.fgtida.file_safety.scan_stream', return_value=('ClamAV test', '')):
            response = self.client.post('/admin/fgtida/contribution/', {**payload,
                'confirm_rebuild': 'yes', 'acknowledge': 'on', 'reason': 'Upgrade the older converter output to the reviewed scaffold.'})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.reference, reference)
        self.assertEqual(item.document_id, document_id)
        self.assertNotEqual(item.document.rev, revision)
        self.assertEqual(original_path.read_bytes(), slim)
        self.assertIsNone(item.conversion_verified_at)
        self.assertEqual(item.source_manifest['package_profile'], PROFILE)
        self.assertIn('conf.py', unpack(bytes(item.source_archive)))
        self.assertFalse(convert_existing_word(item.pk, self.user, rebuild=True, reason='Already upgraded.'))

    def test_rebuild_does_not_replace_later_work_item_edits(self):
        item, _ = self.old_conversion()
        edited = pack({'index.rst': b'Later reviewed source edits.'})
        with override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=False):
            core.save_document(item.document, core.actor_for(self.user), 'Later source edit.', data=edited, filename='source.zip')
        with self.assertRaisesMessage(ValidationError, 'differs from this conversion'):
            convert_existing_word(item.pk, self.user, rebuild=True, reason='Attempt to rebuild an older original.')
        self.assertEqual(Path(item.document.get_file_name()).read_bytes(), edited)
