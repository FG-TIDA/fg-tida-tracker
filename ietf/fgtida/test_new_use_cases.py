from io import StringIO
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from ietf.doc.models import Document
from ietf.group.models import Group

from . import tests as native_tests
from .github_catalog import sync_repository
from .models import GitHubAccount, GitHubRepository, GitHubSource, Contribution, OutgoingMail
from .source_packages import unpack


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=False, FGTIDA_MAIL_ENABLED=False)
class NewUseCaseTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def repository(self):
        return GitHubRepository.objects.create(account=GitHubAccount.objects.create(organization='fg-tida-wg0'),
            name='use-cases', mode='issues', group=Group.objects.get(acronym='tidawg0'),
            group_evidence='https://github.com/fg-tida-wg0/use-cases', category='use-cases',
            category_evidence='https://github.com/fg-tida-wg0/use-cases')

    def issue(self, number):
        return {'number': number, 'title': f'Use case {number}',
            'html_url': f'https://github.com/fg-tida-wg0/use-cases/issues/{number}',
            'body': '## Proposer(s)\n\nExample Author (Example Institute)\n\n## Summary\n\nAn agent verifies authority before proceeding.\n\n## Keywords\n\nIdentity, authority\n\n## Details\n\nOriginal technical content.\n',
            'labels': [{'name': 'use-case'}], 'updated_at': '2026-11-26T12:00:00Z', 'state': 'open'}

    def test_new_only_preserves_existing_exclusions_and_legacy_records_and_is_repeatable(self):
        repo = self.repository()
        existing = self.issue(1)
        client = Mock()
        client.pages.return_value = [existing]
        sync_repository(repo, client=client, metadata={'id': 456})
        source = GitHubSource.objects.get(repository=repo, source_key='issue:1')
        doc = Document.objects.get(pk=source.document_id)
        before = (doc.rev, doc.title, doc.uploaded_filename, source.checksum, doc.tida_versions.count())
        GitHubSource.objects.create(repository=repo, source_key='issue:2', sync_mode='excluded')
        legacy = Document.objects.create(name='legacy-unlinked-use-case', title='Legacy', group=repo.group,
            type_id='tida', external_url='https://github.com/FG-TIDA/use-cases/issues/3')
        existing['body'] = 'Changed existing issue which must not overwrite the tracker.'
        client.pages.return_value = [existing, self.issue(2), self.issue(3), self.issue(4),
                                    {**self.issue(5), 'pull_request': {}}]
        count = Document.objects.count()
        plan = sync_repository(repo, dry_run=True, client=client, metadata={'id': 456}, new_only=True, template_issues=True)
        self.assertIn('issue #4: create', plan)
        self.assertEqual(Document.objects.count(), count)
        result = sync_repository(repo, client=client, metadata={'id': 456}, new_only=True, template_issues=True)
        self.assertIn('issue #4: created', result)
        self.assertEqual(Document.objects.count(), count + 1)
        source.refresh_from_db()
        doc.refresh_from_db()
        self.assertEqual((doc.rev, doc.title, doc.uploaded_filename, source.checksum, doc.tida_versions.count()), before)
        self.assertIsNone(GitHubSource.objects.get(repository=repo, source_key='issue:2').document_id)
        self.assertFalse(GitHubSource.objects.filter(repository=repo, source_key='issue:3').exists())
        self.assertEqual(Document.objects.get(pk=legacy.pk).rev, legacy.rev)
        added = GitHubSource.objects.select_related('document').get(repository=repo, source_key='issue:4')
        self.assertEqual(added.document.abstract, '')
        from .file_safety import preview_source
        archive, filename = preview_source(added.document)
        files = unpack(archive)
        for name in ('index.rst', 'conf.py', 'original/github-issue.md', 'requirements.txt'):
            self.assertIn(name, files)
        self.assertIn(b'Original technical content.', files['index.rst'])
        self.assertIn(b'## Proposer(s)', files['original/github-issue.md'])
        from .submission_template import problems
        self.assertEqual(problems(files['index.rst'].decode()), [])
        item = Contribution.objects.get(document=added.document)
        self.assertEqual(item.number_prefix, 'FG-TIDA-WG0')
        self.assertEqual(item.sequence_number, Contribution.objects.get(document=doc).sequence_number + 1)
        sync_repository(repo, client=client, metadata={'id': 456}, new_only=True, template_issues=True)
        self.assertEqual(Document.objects.count(), count + 1)
        # A later regular sync must not replace the prepared package with raw MD.
        client.pages.return_value = [self.issue(4)]
        sync_repository(repo, client=client, metadata={'id': 456})
        added.refresh_from_db()
        self.assertTrue(added.source_metadata['issue_template'])
        self.assertEqual(added.document.tida_versions.count(), 1)
        self.assertFalse(OutgoingMail.objects.exists())

    def test_scanner_failure_does_not_create_a_partial_record(self):
        repo = self.repository()
        client = Mock()
        client.pages.return_value = [self.issue(1)]
        count = Document.objects.count()
        with patch('ietf.fgtida.issue_packages.prepare_issue', side_effect=ValidationError('File is awaiting scanning.')):
            with self.assertRaises(ValidationError):
                sync_repository(repo, client=client, metadata={'id': 456}, new_only=True, template_issues=True)
        self.assertEqual(Document.objects.count(), count)
        self.assertFalse(GitHubSource.objects.filter(repository=repo).exists())

    def test_template_option_requires_explicit_new_only_wg0_scope(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('sync_fgtida_github', template_issues=True, stdout=StringIO())
