import hashlib
import io
import json
from pathlib import Path
from django.core.management import call_command
from django.test import TestCase, override_settings
from ietf.doc.models import Document
from ietf.group.models import Group
from . import core, tests as native_tests
from .models import GitHubAccount, GitHubRepository, GitHubSource, WorkingGroupConfiguration
from .github_catalog import store_source


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False)
class SourceClassificationTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def repository(self, **values):
        account, _ = GitHubAccount.objects.get_or_create(organization='FG-TIDA')
        return GitHubRepository.objects.create(account=account, name='use-cases', group=Group.objects.get(acronym='tidawg0'), mode='issues', category='use-cases', **values)

    def test_unproven_group_category_and_body_mentions_do_not_assign_wg(self):
        repo = self.repository()
        args = (repo, 'issue:1', b'# A use case mentioning WG1 and WG5', 'issue-1.md', 'A use case', repo.url+'/issues/1', 'updated')
        store_source(*args)
        doc = GitHubSource.objects.get(repository=repo).document
        self.assertEqual(doc.group_id, self.root.pk)
        self.assertFalse(doc.tags.exists())
        self.assertEqual(doc.rev, '00')

    def test_labels_are_evidence_but_unknown_or_multiple_groups_are_not_invented(self):
        repo = self.repository(category_evidence='https://github.com/FG-TIDA/use-cases#readme')
        WorkingGroupConfiguration.objects.create(group=Group.objects.get(acronym='tidawg1'), number=1, topic_acronym='cdra')
        args = (repo, 'issue:1', b'# A proposal', 'issue-1.md', 'A proposal', repo.url+'/issues/1', 'updated')
        store_source(*args, source_metadata={'labels':['wg5'], 'state':'open'})
        source = GitHubSource.objects.get(repository=repo)
        self.assertEqual(source.document.group_id, self.root.pk)
        self.assertFalse(WorkingGroupConfiguration.objects.filter(number=5).exists())
        store_source(*args, source_metadata={'labels':['wg1'], 'state':'closed'})
        source.refresh_from_db()
        self.assertEqual(source.document.group.acronym, 'tidawg1')
        self.assertEqual(source.document.rev, '00')
        self.assertEqual(source.source_metadata['classification']['group_evidence'], repo.url+'/issues/1')
        self.assertEqual(source.source_metadata['state'], 'closed')
        self.assertTrue(source.document.tags.filter(slug='tida-use-cases').exists())
        store_source(*args, source_metadata={'labels':['wg1','wg5']})
        source.refresh_from_db()
        self.assertEqual(source.document.group_id, self.root.pk)

    def test_correction_is_read_only_by_default_and_keeps_archives_and_manual_overrides(self):
        repo = self.repository()
        doc = Document(name='legacy-wg0-case', title='Legacy case', type_id='tida', group=repo.group)
        data = b'Original source mentioning WG2 is not a WG2 assignment.'
        core.save_document(doc, core.actor_for(), 'Legacy import', data=data, filename='source.txt')
        doc.tags.add('tida-use-cases')
        source = GitHubSource.objects.create(repository=repo, document=doc, source_key='issue:1', source_url=repo.url+'/issues/1', checksum=hashlib.sha256(data).hexdigest())
        manual = Document.objects.create(name='manual-case', title='Manual case', group=repo.group, type_id='tida')
        GitHubSource.objects.create(repository=repo, document=manual, source_key='issue:2', sync_mode='manual')
        before = list(doc.tida_versions.values_list('revision','version','recorded'))
        output = io.StringIO()
        call_command('audit_fgtida_classification', skip_checks=True, stdout=output)
        doc.refresh_from_db()
        repo.refresh_from_db()
        self.assertEqual(doc.group.acronym, 'tidawg0')
        self.assertEqual(repo.group.acronym, 'tidawg0')
        call_command('audit_fgtida_classification', apply=True, skip_checks=True, stdout=io.StringIO())
        doc.refresh_from_db()
        repo.refresh_from_db()
        source.refresh_from_db()
        manual.refresh_from_db()
        self.assertEqual(doc.group_id, self.root.pk)
        self.assertEqual(repo.group_id, self.root.pk)
        self.assertEqual(manual.group.acronym, 'tidawg0')
        self.assertEqual(Path(doc.get_file_name()).read_bytes(), data)
        self.assertEqual(before, list(doc.tida_versions.values_list('revision','version','recorded')))
        self.assertEqual(doc.rev, '00')
        self.assertEqual(source.source_metadata['classification']['category_evidence'], 'https://github.com/FG-TIDA/use-cases#readme')
        count = doc.docevent_set.count()
        call_command('audit_fgtida_classification', apply=True, skip_checks=True, stdout=io.StringIO())
        self.assertEqual(doc.docevent_set.count(), count)

    def test_attachments_do_not_become_proposals_and_fresh_defaults_do_not_fill_wg0(self):
        call_command('configure_fgtida_github', skip_checks=True, stdout=io.StringIO())
        repo = GitHubRepository.objects.get(name='use-cases')
        self.assertEqual(repo.group_id, self.root.pk)
        store_source(repo, 'attachment:abc', b'Supporting material', 'attachment.txt', 'Supporting material', repo.url+'/files/1/attachment.txt', 'sha')
        doc = GitHubSource.objects.get(repository=repo).document
        self.assertFalse(doc.tags.filter(slug='tida-use-cases').exists())
        self.assertFalse(core.documents().filter(group__acronym='tidawg0').exists())
        for repo in GitHubRepository.objects.filter(document_type='charter'):
            self.assertEqual(repo.category, '')
            self.assertTrue(repo.group_evidence)

    def test_old_fixture_without_integration_rows_can_be_corrected_without_reimport(self):
        doc = Document.objects.create(name='old-fixture-case', title='Old case', type_id='tida',
            group=Group.objects.get(acronym='tidawg0'), external_url='https://github.com/FG-TIDA/use-cases/issues/4')
        doc.tags.add('tida-use-cases')
        core.save_document(doc, core.actor_for(), 'Old automated import')
        call_command('audit_fgtida_classification', apply=True, skip_checks=True, stdout=io.StringIO())
        doc.refresh_from_db()
        self.assertEqual(doc.group_id, self.root.pk)
        self.assertTrue(doc.tags.filter(slug='tida-use-cases').exists())
        self.assertFalse(doc.github_sources.exists())
