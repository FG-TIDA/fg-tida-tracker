import io
import subprocess
import zipfile
import base64
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from ietf.group.models import Group
from .source_packages import pack, unpack, prepare_zip, convert_word, github_snapshot
from .models import Contribution, ContributionRecipient, OutgoingMail
from . import tests as native_tests


class PackageTests(SimpleTestCase):
    def test_nested_package_preserves_assets_and_rejects_missing_images(self):
        data = pack({'draft/src/doc/index.rst': b'Title\n=====\n\n.. image:: figures/a.png\n',
                     'draft/src/doc/figures/a.png': b'example image'})
        source, manifest = prepare_zip(data, 'draft/src/doc/index.rst', 'draft/src/doc')
        self.assertEqual(manifest['entry_document'], 'index.rst')
        self.assertEqual(unpack(source)['figures/a.png'], b'example image')
        with self.assertRaises(ValidationError):
            prepare_zip(pack({'index.rst': b'.. image:: missing.png'}), 'index.rst')
        with self.assertRaises(ValidationError):
            prepare_zip(pack({'index.rst': b'.. |figure| image:: missing.png'}), 'index.rst')

    def test_traversal_symlinks_duplicates_and_zip_bombs_rejected(self):
        for name in ['../index.rst', '/index.rst', 'a\\index.rst', 'C:/index.rst']:
            with self.assertRaises(ValidationError):
                unpack(pack({name: b'bad'}))
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            entry = zipfile.ZipInfo('index.rst')
            entry.external_attr = 0o120777 << 16
            z.writestr(entry, '../secret')
        with self.assertRaises(ValidationError):
            unpack(stream.getvalue())
        with self.assertRaises(ValidationError):
            unpack(pack({'index.rst': b'a' * (31 * 1024 * 1024)}))
        with self.assertRaises(ValidationError):
            unpack(pack({'index.rst': b'a', 'INDEX.rst': b'b'}))

    def test_github_fetch_is_pinned_bounded_and_does_not_forward_credentials(self):
        archive = pack({'repo-sha/src/doc/index.rst': b'Exact commit', 'repo-sha/.github/workflows/docs.yml': b'not imported'})
        response = MagicMock(status_code=200)
        response.iter_content.return_value = [archive]
        response.__enter__.return_value = response
        with patch('ietf.fgtida.source_packages.requests.get', return_value=response) as get:
            source, manifest = github_snapshot('https://github.com/alice/draft', 'a'*40, 'src/doc', 'src/doc/index.rst')
            self.assertEqual(unpack(source), {'index.rst': b'Exact commit'})
            self.assertEqual(manifest['commit'], 'a'*40)
            self.assertEqual(get.call_args.args[0], 'https://codeload.github.com/alice/draft/zip/'+'a'*40)
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
            self.assertNotIn('headers', get.call_args.kwargs)
        for url, sha in [('https://evil.test/a/b', 'a'*40), ('https://github.com/a/b', 'main')]:
            with self.assertRaises(ValidationError):
                github_snapshot(url, sha, 'src/doc', 'src/doc/index.rst')

    def test_real_pandoc_word_conversion_and_tracked_changes_rejection(self):
        word = subprocess.run(['pandoc', '-f', 'rst', '-t', 'docx'], input=b'Example\n=======\n\nA **normative** paragraph.\n', capture_output=True, check=True).stdout
        source, manifest = convert_word(word)
        self.assertIn(b'normative', unpack(source)['index.rst'])
        self.assertIn(b'Example', unpack(source)['index.rst'])
        self.assertEqual(manifest['entry_document'], 'index.rst')
        files = unpack(word)
        files['word/document.xml'] = files['word/document.xml'].replace(b'<w:body>', b'<w:body><w:ins/>', 1)
        with self.assertRaises(ValidationError):
            convert_word(pack(files))

    def test_word_images_are_extracted_and_external_media_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWZkAAAAASUVORK5CYII=')
            (root / 'figure.png').write_bytes(image)
            word = subprocess.run(['pandoc', '-f', 'rst', '-t', 'docx'], cwd=root,
                input=b'Illustrated\n===========\n\n.. image:: figure.png\n', capture_output=True, check=True).stdout
        source, manifest = convert_word(word)
        files = unpack(source)
        self.assertTrue(any(value == image for value in files.values()))
        self.assertIn(b'image::', files['index.rst'])
        files = unpack(word)
        files['word/_rels/document.xml.rels'] = b'<Relationships><Relationship TargetMode="External" Type="image" Target="http://127.0.0.1/private"/></Relationships>'
        with self.assertRaises(ValidationError):
            convert_word(pack(files))


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_MAIL_ENABLED=False, FGTIDA_MAIL_IMMEDIATE=False)
class SubmissionTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp
    source_submission = native_tests.NativeDatatrackerTests.source_submission

    def payload(self):
        return {'title': 'New specification', 'group': Group.objects.get(acronym='tidawg1').pk,
                **self.source_submission()}

    def test_rst_archives_source_and_preserves_notification_and_acknowledgement(self):
        from .mail import deliver
        from django.core import mail
        group = Group.objects.get(acronym='tidawg1')
        ContributionRecipient.objects.create(group=group, email='wg1@example.test')
        response = self.client.post('/submit/', self.payload())
        self.assertEqual(response.status_code, 302)
        item = Contribution.objects.get()
        self.assertEqual(unpack(bytes(item.source_archive))['index.rst'], b'Contribution')
        self.assertEqual(item.document.states.get(type_id='tida').slug, 'submitted')
        self.assertEqual(set(OutgoingMail.objects.filter(contribution=item).values_list('purpose', flat=True)), {'group', 'submitter'})
        with override_settings(FGTIDA_MAIL_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            for message in OutgoingMail.objects.filter(contribution=item):
                self.assertTrue(deliver(message.pk))
                self.assertFalse(deliver(message.pk))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].attachments[0].filename, 'proposal.zip')
        self.assertContains(self.client.get(response.url), 'Source reading preview')

    def test_word_original_retained_and_only_submitter_can_verify_before_adoption(self):
        from .contributions import accept
        word = subprocess.run(['pandoc', '-f', 'rst', '-t', 'docx'], input=b'Proposal\n========\n\nText.', capture_output=True, check=True).stdout
        payload = self.payload()
        payload.update(submission_method='word', entry_document='', upload=SimpleUploadedFile('proposal.docx', word))
        response = self.client.post('/submit/', payload)
        self.assertEqual(response.status_code, 302)
        item = Contribution.objects.get()
        self.assertEqual(bytes(item.content), word)
        self.assertIn('index.rst', unpack(bytes(item.source_archive)))
        with self.assertRaises(ValidationError):
            accept(item.pk, self.user)
        self.assertEqual(self.client.get(f'/submit/{item.pk}/verify/').status_code, 405)
        stranger = get_user_model().objects.create_user('other', password='test-password')
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(f'/submit/{item.pk}/source/').status_code, 404)
        self.assertEqual(self.client.post(f'/submit/{item.pk}/verify/', {'verified': 'yes'}).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(f'/submit/{item.pk}/verify/', {'verified': 'yes'}).status_code, 302)
        accept(item.pk, self.user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'accepted')

    def test_invalid_submission_does_not_create_records_or_mail(self):
        payload = self.payload()
        payload['upload'] = SimpleUploadedFile('bad.zip', b'not a zip')
        self.assertContains(self.client.post('/submit/', payload), 'Cannot read this ZIP')
        self.assertFalse(Contribution.objects.exists())
        self.assertFalse(OutgoingMail.objects.exists())

    def test_editor_can_classify_rst_packages_as_input_documents(self):
        from .admin import DocumentForm
        form = DocumentForm({'name': 'rst-input', 'title': 'RST input', 'type': 'tida', 'group': self.root.pk,
                             'document_direction': 'input', 'comment_about_changes': 'Source contribution'},
                            {'upload': self.source_submission()['upload']})
        self.assertTrue(form.is_valid(), form.errors)

    def test_github_form_preserves_commit_and_needs_no_upload(self):
        source, manifest = prepare_zip(pack({'index.rst': b'Snapshot'}), 'index.rst')
        manifest['commit'] = 'a'*40
        payload = self.payload()
        payload.pop('upload')
        payload.update(submission_method='github', draft_repository_url='https://github.com/alice/proposal',
                       source_commit='a'*40, source_folder='src/doc', entry_document='src/doc/index.rst')
        with patch('ietf.fgtida.source_packages.github_snapshot', return_value=(source, manifest)):
            self.assertEqual(self.client.post('/submit/', payload).status_code, 302)
        item = Contribution.objects.get()
        self.assertEqual(item.source_commit, 'a'*40)
        self.assertEqual(bytes(item.content), source)

    @override_settings(SITE_URL='https://tracker.example.test')
    def test_adoption_pr_imports_source_assets_but_not_submitted_configuration(self):
        from .github_jobs import bootstrap_repository
        from .models import GitHubAccount, GitHubRepository, WorkItem
        self.assertEqual(self.client.post('/submit/', self.payload()).status_code, 302)
        contribution = Contribution.objects.get()
        contribution.source_archive, contribution.source_manifest = prepare_zip(pack({
            'index.rst': b'Reviewed proposal', 'figures/diagram.svg': b'<svg/>', 'conf.py': b'raise RuntimeError()'}), 'index.rst')
        contribution.save()
        account = GitHubAccount.objects.create(organization='fg-tida-wg1')
        repo = GitHubRepository.objects.create(account=account, group=contribution.group, name='wg1-topic-proposal')
        item = WorkItem.objects.get(document=contribution.document)
        item.repository = repo
        item.save()
        client = MagicMock()
        def request(method, path, data=None):
            if '/git/ref/heads/' in path:
                return {'object': {'sha': 'b'*40}}
            if '/contents/src/doc/conf.py?' in path:
                return {'content': base64.b64encode(b'project = "Template"\n').decode(), 'sha': 'c'*40}
            return None
        client.request.side_effect = request
        with patch('ietf.fgtida.github_jobs.update_state'):
            bootstrap_repository(item, client)
        writes = {call.args[1]: call.args[2] for call in client.request.call_args_list if call.args[0] == 'PUT'}
        prefix = '/repos/fg-tida-wg1/wg1-topic-proposal/contents/'
        self.assertEqual(base64.b64decode(writes[prefix+'src/doc/index.rst']['content']), b'Reviewed proposal')
        self.assertEqual(base64.b64decode(writes[prefix+'src/doc/figures/diagram.svg']['content']), b'<svg/>')
        self.assertNotIn(b'RuntimeError', base64.b64decode(writes[prefix+'src/doc/conf.py']['content']))
        self.assertTrue(all(value['branch'].startswith('tracker/') for value in writes.values()))
