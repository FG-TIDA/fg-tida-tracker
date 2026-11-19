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
from .models import DocumentListing, PublicDocumentType, WorkingGroupConfiguration, GitHubAccount, GitHubRepository, GitHubSource
from . import tests as native_tests


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False)
class DocumentListTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp
    admin_payload = native_tests.NativeDatatrackerTests.admin_payload

    def test_source_types_custom_categories_and_corrected_versions_preserve_downloads(self):
        from ietf.name.models import DocTagName
        from .versioning import public_version

        for category in ['use-cases', 'themes']:
            document = Document(name='classified-' + category, title='Example ' + category, group=self.root, type_id='tida')
            core.save_document(document, core.actor_for(), 'Initial source', data=b'Initial content', filename='source.txt')
            document.tags.add('tida-' + category)
            detail = self.client.get(f'/doc/{document.name}/')
            self.assertNotContains(detail, '<th scope="row">Type</th>')
            self.assertContains(detail, '<th scope="row">Working group / stream</th><td></td>')
            self.assertNotContains(detail, 'wg assignment evidence')
            self.assertNotContains(detail, 'FG-TIDA (no wg assigned)')
            kind = PublicDocumentType.objects.get(name={'use-cases': 'Use case', 'themes': 'Theme'}[category])
            self.assertContains(self.client.get('/doc/', {'type': f'public-{kind.pk}'}), document.title)
        response = self.client.post('/admin/fgtida/publicdocumenttype/add/', {'name': 'Report'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PublicDocumentType.objects.filter(name='Report').exists())
        edit = f'/admin/doc/document/{document.pk}/change/'
        payload = self.admin_payload(edit, {'name': document.name, 'title': document.title, 'type': document.type_id,
            'group': self.root.pk, 'current_version': '0.9', 'comment_about_changes': 'Correct version and type'})
        self.assertEqual(self.client.post(edit, payload).status_code, 302)
        document.refresh_from_db()
        self.assertEqual(public_version(document), '0.9')
        for version in ['0.0', '0.9']:
            self.assertEqual(self.client.get(f'/doc/{document.name}/download/{version}/').status_code, 200)
        from .models import DocumentAttribution
        DocumentAttribution.objects.update_or_create(document=document, defaults={'public_type': PublicDocumentType.objects.get(name='Report')})
        listing = self.client.get('/doc/', {'type': 'public-' + str(PublicDocumentType.objects.get(name='Report').pk)})
        self.assertContains(listing, document.title)
        self.assertFalse(listing.context['groups'].filter(acronym='tida').exists())
        self.assertNotContains(self.client.get(f'/doc/{document.name}/'), '<th scope="row">Category</th>')

    def test_preview_uses_one_title_and_identification_metadata_with_editor_overrides(self):
        title = 'Example use case'
        body = (f'# {title}\n\n## 1. Identification\n\n'
                '**Submitting organization:** Individual expert contribution (FG-TIDA participant)\n\n'
                '**Contact name:** Example Author\n\n'
                '**Contact email:** [author@example.test](mailto:author@example.test).\n\n'
                '## Technical scope\n\nKeep the rest of the document here.\n')
        document = Document(name='preview-identification', title=title, group=self.root, type_id='tida', abstract=body)
        core.save_document(document, core.actor_for(self.user), 'Initial content', data=body.encode(), filename='preview.md')
        url = f'/doc/{document.name}/'
        response = self.client.get(url)
        self.assertContains(response, f'>{title}</h1>', count=1)
        self.assertNotContains(response, f'>{title}</h2>')
        self.assertContains(response, 'Keep the rest of the document here.', count=1)
        self.assertContains(response, '<th scope="row">Authors</th><td>Example Author</td>')
        self.assertContains(response, '<th scope="row">Affiliation</th><td>Individual expert</td>')
        self.assertContains(response, '<th scope="row">Submission contact</th><td><a href="mailto:author@example.test">')

        self.assertEqual(self.client.post('/admin/fgtida/publicdocumenttype/add/', {'name': 'Technical note'}).status_code, 302)
        public_type = PublicDocumentType.objects.get(name='Technical note')
        edit = f'/admin/doc/document/{document.pk}/change/'
        values = self.admin_payload(edit, {'name': document.name, 'title': title, 'type': 'tida', 'group': self.root.pk,
            'comment_about_changes': 'Update public metadata'})
        values.update({'tida_attribution-TOTAL_FORMS': 1, 'tida_attribution-0-public_type': public_type.pk,
            'tida_attribution-0-authors': 'Edited Author', 'tida_attribution-0-contact_emails': 'editor@example.test'})
        saved = self.client.post(edit, values)
        self.assertEqual(saved.status_code, 302)
        detail = self.client.get(url + 'revision/0.0/')
        self.assertNotContains(detail, '<th scope="row">Type</th>')
        self.assertContains(detail, '<th scope="row">Authors</th><td>Edited Author</td>')
        self.assertContains(detail, 'mailto:editor@example.test')
        self.assertContains(self.client.get('/doc/', {'type': f'public-{public_type.pk}'}), title)
        self.assertEqual(self.client.get(url + 'download/0.0/').status_code, 200)

    def word_file(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
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
        for value in ['LS Inputs', 'Word', 'FG-TIDA plenary', 'Example organization',
                      'FG-TIDA-I-001', '/doc/fg-tida-i-001/download/0.0/']:
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
        for label in ['WG42 : Future topic', 'NWI for Incubation', 'Submit NWI(New Work Item)', 'LS Inputs', 'LS Outputs', 'Upcoming meetings', 'Past meetings']:
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
