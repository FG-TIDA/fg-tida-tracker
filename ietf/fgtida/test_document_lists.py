"""Exercise editorial uploads, public downloads and future numbered groups."""
import io
import zipfile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from ietf.doc.models import Document, State
from ietf.group.models import Group
from ietf.meeting.models import Meeting
from . import core
from .models import DocumentListing, WorkingGroupConfiguration, GitHubAccount, GitHubRepository, GitHubSource
from . import tests as native_tests


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False)
class DocumentListTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp
    admin_payload = native_tests.NativeDatatrackerTests.admin_payload

    def word_file(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('word/document.xml', '<document><p>Original contribution</p></document>')
        return SimpleUploadedFile('contribution.docx', data.getvalue())

    def test_editor_can_publish_update_reclassify_and_remove_listing(self):
        meeting = Meeting.objects.create(number='input-meeting', title='FG-TIDA plenary', type_id='tida', date=timezone.localdate(), days=1, time_zone='UTC')
        values = self.admin_payload('/admin/doc/document/add/', {
            'name': 'fg-tida-i-001', 'title': 'Input contribution', 'type': 'tida', 'group': self.root.pk,
            'upload': self.word_file(), 'comment_about_changes': 'Publish input',
            'document_direction': 'input', 'document_meeting': meeting.pk, 'document_source': 'Example organization',
            'document_created_0': '2026-11-01', 'document_created_1': '12:00:00', '_save': 'Save'})
        response = self.client.post('/admin/doc/document/add/', values)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['adminform'].form.errors)
        doc = Document.objects.get(name='fg-tida-i-001')
        listing = DocumentListing.objects.get(document=doc)
        self.assertEqual(listing.created.date().isoformat(), '2026-11-01')
        self.client.logout()
        response = self.client.get('/doc/?collection=input')
        for value in ['LS Inputs', 'Word', 'FG-TIDA plenary', 'Example organization', '/doc/fg-tida-i-001/download/0.0/']:
            self.assertContains(response, value)
        self.assertNotContains(self.client.get('/doc/?collection=output'), 'Input contribution')
        download = self.client.get('/doc/fg-tida-i-001/download/0.0/')
        self.assertEqual(download.status_code, 200)
        self.assertTrue(b''.join(download.streaming_content).startswith(b'PK'))
        self.client.force_login(self.user)
        url = f'/admin/doc/document/{doc.pk}/change/'
        values.update({'document_direction': 'output', 'title': 'Final output', 'upload': self.word_file(), 'version': '1.0', 'comment_about_changes': 'Publish output'})
        values = self.admin_payload(url, values)
        self.assertEqual(self.client.post(url, values).status_code, 302)
        self.assertNotContains(self.client.get('/doc/?collection=input'), 'Final output')
        self.assertContains(self.client.get('/doc/?collection=output'), '/doc/fg-tida-i-001/download/1.0/')
        self.assertEqual(self.client.get('/doc/fg-tida-i-001/download/0.0/').status_code, 200)
        values.pop('upload')
        values.pop('version')
        values['document_direction'] = ''
        self.assertEqual(self.client.post(url, values).status_code, 302)
        self.assertFalse(DocumentListing.objects.filter(document=doc).exists())
        self.assertEqual(self.client.get('/doc/fg-tida-i-001/').status_code, 200)

    def test_listing_rejects_non_document_and_out_of_scope_meeting(self):
        from .admin import DocumentForm
        from ietf.name.models import MeetingTypeName
        MeetingTypeName.objects.get_or_create(slug='ietf', defaults={'name': 'IETF'})
        other = Meeting.objects.create(number='unrelated-meeting', type_id='ietf', date=timezone.localdate(), days=1, time_zone='UTC')
        form = DocumentForm({'name': 'invalid-input', 'title': 'Invalid input', 'type': 'tida', 'group': self.root.pk,
            'document_direction': 'input', 'document_meeting': other.pk, 'comment_about_changes': 'Invalid'},
            {'upload': SimpleUploadedFile('file.txt', b'Not a Word or PDF file')})
        self.assertFalse(form.is_valid())
        self.assertIn('upload', form.errors)
        self.assertIn('document_meeting', form.errors)

    def test_future_group_navigation_and_collection_search_preserve_scope(self):
        group = Group.objects.create(acronym='tidawg42', name='Future topic', type_id='wg', state_id='active', parent=self.root)
        WorkingGroupConfiguration.objects.create(group=group, number=42)
        page = self.client.get('/')
        for label in ['WG42 : Future topic', 'No working group yet', 'Submit a contribution', 'LS Inputs', 'LS Outputs', 'Upcoming meetings', 'Past meetings']:
            self.assertContains(page, label)
        for removed in ['Proposed groups', 'Concluded groups', 'Meeting archive', 'Submit a proposal / contribution']:
            self.assertNotContains(page, removed)
        self.assertContains(self.client.get('/doc/?collection=input&name=example'), 'name="collection" value="input"')
        self.assertContains(self.client.get('/doc/?collection=input'), 'fg-mobile-cards')

    def test_file_catalog_versions_update_for_templates_charters_and_deliverables(self):
        from .github_catalog import store_source
        account = GitHubAccount.objects.create(organization='example-public')
        for kind in ['tida-template', 'charter', 'tida']:
            repo = GitHubRepository.objects.create(account=account, name='repo-'+kind, group=self.root, mode='files', document_type=kind)
            store_source(repo, 'file:document.md', b'# Initial content', 'document.md', 'Initial content', repo.url+'/blob/main/document.md', 'a'*40)
            source = GitHubSource.objects.get(repository=repo)
            store_source(repo, source.source_key, b'# Updated content', 'document.md', 'Updated content', source.source_url, 'b'*40)
            source.document.refresh_from_db()
            self.assertEqual(list(source.document.tida_versions.order_by('version').values_list('version', flat=True)), ['0.0', '0.1'])
            source.sync_mode = 'manual'
            source.save()
            self.assertEqual(store_source(repo, source.source_key, b'# Upstream change', 'document.md', 'Upstream change', source.source_url, 'c'*40), 'manual/excluded')
