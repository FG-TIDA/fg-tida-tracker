import hashlib
from io import StringIO
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from ietf.doc.models import Document
from . import core, tests as native_tests
from .github_attribution import source_attribution
from .credits import resolve_credits
from .models import Contribution, DocumentAttribution, OutgoingMail
from .source_packages import pack, unpack, validate_sources
from .word_scaffold import scaffold, complete_rst_source


class IdentityAndPackageTests(SimpleTestCase):
    def test_contact_table_identity_is_split_into_real_fields(self):
        text = '| **Contact** | Name: Example Expert / Organization: Independent expert / Country: Example Country / E-mail: expert@example.test |'
        values = source_attribution(text)
        self.assertEqual(values['authors'], ['Example Expert'])
        self.assertEqual(values['contacts'], ['Example Expert'])
        self.assertEqual(values['affiliations'], ['Independent expert'])
        self.assertEqual(values['contact_emails'], ['expert@example.test'])
        credits, _, _ = resolve_credits(text, None, {}, [])
        self.assertEqual(credits['authors'], ['Example Expert'])

    def test_rst_author_table_cannot_override_clean_names_with_identity_blocks(self):
        text = 'Authors\n-------\n\n.. list-table::\n\n   * - Name\n     - Affiliation\n   * - Name: Example Expert / Organization: Example Lab / Country: Example Country / E-mail:\n     - Example Lab\n'
        values = source_attribution(text)
        self.assertEqual(values['authors'], ['Example Expert'])
        self.assertEqual(values['affiliations'], ['Example Lab'])

    def test_completing_rst_preserves_all_original_sources_and_media(self):
        original = {'index.rst': b'Actual contribution\n===================\n\n.. image:: figures/diagram.png\n',
                    'figures/diagram.png': b'existing image bytes', 'chapters/appendix.rst': b'Actual appendix'}
        files, metadata = complete_rst_source(original, 'index.rst')
        self.assertTrue(set(scaffold()[1]).issubset(files))
        for name, content in original.items():
            self.assertEqual(files[name], content)
        self.assertNotIn('chapters/01_contribution.rst', files)
        validate_sources(files, 'index.rst')
        self.assertEqual(complete_rst_source(files, 'index.rst')[0], files)
        self.assertIn('conf.py', metadata['added_template_files'])


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=False)
class ContributionIntegrityTests(TestCase):
    def word_original(self):
        import subprocess
        return subprocess.run(['pandoc', '-f', 'rst', '-t', 'docx'],
            input=b'Word proposal\n=============\n\nRetained paragraph.\n', capture_output=True, check=True).stdout

    def test_bulk_completion_includes_word_documents_without_a_zip(self):
        from .contribution_packages import inspect_package
        word = self.word_original()
        doc = Document(name='word-without-source-package', title='Word proposal', type_id='tida', group=self.root)
        core.save_document(doc, core.actor_for(), 'Original Word', data=word, filename='proposal.docx')
        item = Contribution.objects.create(group=self.root, document=doc, title=doc.title,
            filename='proposal.docx', content=word)
        unlinked = Contribution.objects.create(group=self.root, title='Word without document link',
            filename='unlinked.docx', content=word)
        self.assertEqual(inspect_package(item)['state'], 'incomplete')
        call_command('complete_fgtida_packages', word_only=True, apply=True,
            reason='Complete retained Word source packages.', stdout=StringIO())
        item.refresh_from_db()
        self.assertEqual(inspect_package(item)['state'], 'complete')
        self.assertEqual(bytes(item.content), word)
        unlinked.refresh_from_db()
        self.assertEqual(inspect_package(unlinked)['state'], 'complete')
        self.assertIsNotNone(unlinked.document_id)
        self.assertFalse(item.conversion_verified_at)
        self.assertFalse(OutgoingMail.objects.exists())

    def test_word_media_missing_from_otherwise_complete_package_is_rebuilt(self):
        import base64
        from .word_scaffold import wrap_word_source, PROFILE
        from .contribution_packages import inspect_package, complete_package
        word_files = unpack(self.word_original())
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWZkAAAAASUVORK5CYII=')
        word_files['word/media/retained.png'] = image
        word = pack(word_files)
        incomplete = pack(wrap_word_source('Word proposal\n=============\n\nRetained paragraph.', {}))
        doc = Document(name='word-missing-media', title='Word proposal', type_id='tida', group=self.root)
        core.save_document(doc, core.actor_for(), 'Old conversion', data=incomplete, filename='source.zip')
        old_path = Path(doc.get_file_name())
        item = Contribution.objects.create(group=self.root, document=doc, title=doc.title,
            filename='proposal.docx', content=word, source_archive=incomplete, submission_method='word',
            source_manifest={'package_profile': PROFILE, 'sha256': hashlib.sha256(incomplete).hexdigest()})
        self.assertEqual(inspect_package(item)['missing_word_media'], ['word/media/retained.png'])
        self.assertTrue(complete_package(item.pk, self.user, 'Recover all embedded Word images.'))
        item.refresh_from_db()
        self.assertEqual(inspect_package(item)['state'], 'complete')
        self.assertIn(image, unpack(bytes(item.source_archive)).values())
        self.assertEqual(old_path.read_bytes(), incomplete)
        self.assertFalse(complete_package(item.pk, self.user, 'Do not duplicate the complete package.'))

    setUp = native_tests.NativeDatatrackerTests.setUp

    def test_completion_preserves_an_alternative_word_format_on_the_new_revision(self):
        from .models import DocumentFormat
        from .contribution_packages import inspect_package, complete_package
        original = pack({'index.rst': b'Word proposal\n=============\n\nRetained paragraph.'})
        word = self.word_original()
        doc = Document(name='source-with-word-format', title='Word proposal', type_id='tida', group=self.root)
        core.save_document(doc, core.actor_for(), 'Original package', data=original, filename='source.zip')
        DocumentFormat.objects.create(document=doc, revision=doc.rev, kind='docx', filename='original.docx',
            content=word, sha256=hashlib.sha256(word).hexdigest())
        item = Contribution.objects.create(group=self.root, document=doc, title=doc.title,
            filename='source.zip', content=original, submission_method='rst', source_archive=original,
            source_manifest={'entry_document': 'index.rst', 'sha256': hashlib.sha256(original).hexdigest()})
        self.assertTrue(complete_package(item.pk, self.user, 'Preserve the alternative Word original.'))
        item.refresh_from_db()
        current = item.document.tida_formats.get(revision=item.document.rev, kind='docx')
        self.assertEqual(bytes(current.content), word)
        self.assertEqual(inspect_package(item)['state'], 'complete')

    def payload(self, content=b'An actual submission.'):
        from ietf.group.models import Group
        return {'title': 'Account-owned work item', 'group': Group.objects.get(acronym='tidawg1').pk,
                'meeting': native_tests.open_submission_meeting().pk, 'submission_method': 'rst',
                'entry_document': 'index.rst', 'publication_consent': 'on',
                'upload': SimpleUploadedFile('proposal.zip', pack({'index.rst': content}))}

    def test_only_logged_in_user_can_submit_and_cannot_forge_another_submitter(self):
        self.client.logout()
        self.assertEqual(self.client.post('/submit/', self.payload()).status_code, 302)
        self.assertFalse(Contribution.objects.exists())
        member = get_user_model().objects.create_user('ordinary-member', first_name='Example', last_name='Member')
        self.client.force_login(member)
        for forged in ({'submitter': self.user.pk}, {'contact_name': 'Another Person'}, {'contact_email': 'someone@example.test'}):
            self.assertEqual(self.client.post('/submit/', {**self.payload(), **forged}).status_code, 403)
        self.assertFalse(Contribution.objects.exists())
        self.assertEqual(self.client.post('/submit/', self.payload()).status_code, 302)
        self.assertEqual(Contribution.objects.get().submitter_id, member.pk)

    def test_logged_in_submitter_has_clickable_profile_without_guessing_other_authors(self):
        member = get_user_model().objects.create_user('actual-submitter', first_name='Example', last_name='Member')
        other = get_user_model().objects.create_user('namesake-account', first_name='Other', last_name='Expert')
        other_person = core.actor_for(other)
        self.client.force_login(member)
        self.assertEqual(self.client.post('/submit/', self.payload(b'Author: Example Member\nAuthor: Other Expert\n')).status_code, 302)
        item = Contribution.objects.get()
        person = core.actor_for(member)
        self.client.logout()
        page = self.client.get(f'/doc/{item.document.name}/')
        url = reverse('fgtida:person', args=[person.pk])
        self.assertContains(page, f'href="{url}">Example Member</a>')
        self.assertContains(page, 'Submitted by')
        self.assertNotContains(page, f'href="{reverse("fgtida:person", args=[other_person.pk])}"')
        profile = self.client.get(url)
        self.assertEqual(profile.status_code, 200)
        self.assertContains(profile, item.title)
        self.assertIn('conf.py', unpack(bytes(item.source_archive)))

    def test_admin_override_is_cleaned_without_replacing_its_author_identity(self):
        from .catalog_repairs import repair_catalog
        doc = Document(name='contact-metadata-regression', title='Source identity', group=self.root, type_id='tida')
        text = b'| Contact | Name: Example Expert / Organization: Independent expert / Country: Example Country / E-mail: |'
        core.save_document(doc, core.actor_for(), 'Source', data=text, filename='source.rst')
        meta = DocumentAttribution.objects.create(document=doc, authors='Name: Edited Expert / Organization: Edited Lab / Country: Example Country / E-mail:', credit_provenance={'authors': {'mode': 'manual'}})
        repair_catalog(apply=True)
        meta.refresh_from_db()
        self.assertEqual(meta.authors, 'Edited Expert')
        page = self.client.get(f'/doc/{doc.name}/')
        self.assertContains(page, '<td>Edited Expert</td>')
        self.assertNotContains(page, '<td>Name:')

    def test_author_alias_links_only_to_the_actual_submitting_account_contact(self):
        member = get_user_model().objects.create_user('verified-submitter', first_name='Example', last_name='Member', email='verified@example.test')
        self.client.force_login(member)
        self.assertEqual(self.client.post('/submit/', self.payload(b'Author: Example Expert\nContact email: verified@example.test\n')).status_code, 302)
        item = Contribution.objects.get()
        person = core.actor_for(member)
        self.client.logout()
        page = self.client.get(f'/doc/{item.document.name}/')
        self.assertContains(page, f'href="{reverse("fgtida:person", args=[person.pk])}">Example Expert</a>')

    def test_old_incomplete_package_is_completed_as_new_version_without_mail_or_overwrite(self):
        from .contribution_packages import complete_package
        original = pack({'index.rst': b'An original proposal\n====================\n\nOriginal text.'})
        doc = Document(name='legacy-package-regression', title='Old package', group=self.root, type_id='tida')
        core.save_document(doc, core.actor_for(), 'Original', data=original, filename='source.zip')
        item = Contribution.objects.create(group=self.root, document=doc, title=doc.title, content=original,
            filename='original.zip', source_archive=b'', submission_method='legacy', source_manifest={'entry_document': 'index.rst', 'sha256': hashlib.sha256(original).hexdigest()})
        path, old_revision, reference = Path(doc.get_file_name()), doc.rev, item.reference
        mail_count = OutgoingMail.objects.count()
        plan = StringIO()
        call_command('complete_fgtida_packages', contribution=item.pk, stdout=plan)
        self.assertIn('incomplete', plan.getvalue())
        item.refresh_from_db()
        self.assertEqual(bytes(item.source_archive), b'')
        action = {'action': 'complete_source_packages', '_selected_action': item.pk}
        review = self.client.post('/admin/fgtida/contribution/', action)
        self.assertContains(review, 'Complete reviewed contribution packages')
        completed = self.client.post('/admin/fgtida/contribution/', {**action,
            'confirm_complete': 'yes', 'acknowledge': 'on', 'reason': 'Complete missing reviewed template assets.'})
        self.assertEqual(completed.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(bytes(item.content), original)
        self.assertEqual(item.reference, reference)
        self.assertNotEqual(item.document.rev, old_revision)
        files = unpack(bytes(item.source_archive))
        self.assertTrue(set(scaffold()[1]).issubset(files))
        self.assertEqual(files['index.rst'], unpack(original)['index.rst'])
        self.assertEqual(OutgoingMail.objects.count(), mail_count)
        self.assertFalse(complete_package(item.pk, self.user, 'Already complete; keep current package.'))
