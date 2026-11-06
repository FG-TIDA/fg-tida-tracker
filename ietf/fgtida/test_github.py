"""Trust-boundary and document-lifecycle tests; no GitHub writes or real email."""
import hashlib
import io
import json
import tarfile
import time
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import override_settings, SimpleTestCase, TestCase
from ietf.doc.models import Document, State
from . import tests as native_tests
from . import core
from .models import GitHubAccount, GitHubRepository, GitHubSource, WorkItem, WorkItemRelease, GitHubJob
from .github_catalog import eligible, store_source, preserve_manual_document
from .work_items import receive_build, transition, version_tuple


class SourceValidationTests(SimpleTestCase):
    @override_settings(SITE_URL='https://tracker.example.test')
    def test_signed_identity_rejects_wrong_repo_workflow_ref_and_stale_commit(self):
        from jwcrypto import jwk,jwt
        from .github_client import verify_build_identity
        key=jwk.JWK.generate(kty='RSA',size=2048)
        keys=jwk.JWKSet()
        keys.add(jwk.JWK.from_json(key.export_public()))
        repository=SimpleNamespace(github_id=123,full_name='FG-TIDA/example',publish_workflow='.github/workflows/docs.yml',branch='main',account=None)
        now=int(time.time())
        claims={'iss':'https://token.actions.githubusercontent.com','aud':'https://tracker.example.test/integrations/github/build/',
            'iat':now,'nbf':now-1,'exp':now+300,'repository':'FG-TIDA/example','repository_id':'123',
            'ref':'refs/heads/main','sha':'a'*40,'event_name':'push','workflow_ref':'FG-TIDA/example/.github/workflows/docs.yml@refs/heads/main'}
        def signed(changes):
            token=jwt.JWT(header={'alg':'RS256'},claims={**claims,**changes})
            token.make_signed_token(key)
            return token.serialize()
        with patch('django.core.cache.cache.get',return_value=keys.export()),patch('ietf.fgtida.github_client.GitHubClient') as client:
            client.return_value.request.return_value={'sha':'a'*40}
            self.assertEqual(verify_build_identity(signed({}),repository)['sha'],'a'*40)
            for changes in [{'repository_id':'999'},{'event_name':'pull_request'},{'exp':now-100},
                            {'aud':'https://evil.test'},{'workflow_ref':'FG-TIDA/example/.github/workflows/unreviewed.yml@refs/heads/main'},
                            {'sha':'b'*40},{'ref':'refs/heads/feature'}]:
                with self.assertRaises(ValidationError):
                    verify_build_identity(signed(changes),repository)

    def test_attachment_rejects_private_network_redirects(self):
        from .github_client import GitHubClient
        with patch('ietf.fgtida.github_client.installation_token',return_value=None),patch('ietf.fgtida.github_client.requests.get') as get:
            response=get.return_value.__enter__.return_value
            response.status_code=302
            response.headers={'Location':'https://127.0.0.1/private'}
            with self.assertRaises(ValidationError):
                GitHubClient(None).attachment('https://github.com/user-attachments/files/1/test.pdf')
            self.assertEqual(get.call_count,1)
    def test_only_explicit_document_paths(self):
        for path in ['README.md','CHANGELOG.md','HOWTO.md','src/doc/conf.py','.github/test.md','a.exe']:
            self.assertFalse(eligible(path,['*']))
        self.assertTrue(eligible('src/doc/index.rst',['src/doc/index.rst']))
        self.assertFalse(eligible('chapter.md',['src/doc/index.rst']))

    def test_repository_urls_reject_credentials_and_subpaths(self):
        from .github_client import repository_name
        self.assertEqual(repository_name('https://github.com/FG-TIDA/spec-template.git'),'FG-TIDA/spec-template')
        for url in ['http://github.com/o/r','https://evil.test/o/r','https://user@github.com/o/r','https://github.com/o/r/tree/main','https://github.com/o/r?token=secret']:
            with self.assertRaises(ValidationError):
                repository_name(url)

    def test_semantic_release_order(self):
        self.assertLess(version_tuple('v1.9'),version_tuple('v1.10'))
        self.assertEqual(version_tuple('v1.0'),(1,0,0))
        for tag in ['main','v01.0','v1','v1.0.1','v1.0-beta','../v1.0']:
            with self.assertRaises(ValidationError):
                version_tuple(tag)

    def test_archive_traversal_and_symlinks_rejected(self):
        from .github_archives import validate_source_archive
        for name,symlink in [('../escape',False),('/absolute',False),('link',True)]:
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w:gz') as archive:
                item=tarfile.TarInfo(name)
                if symlink:
                    item.type=tarfile.SYMTYPE
                    item.linkname='/etc/passwd'
                archive.addfile(item)
            with self.assertRaises(ValidationError):
                validate_source_archive(stream.getvalue())

    def test_rst_does_not_read_files_or_emit_raw_html(self):
        from .templatetags.fgtida import rich_rst
        html=str(rich_rst('Title\n=====\n\n.. include:: /etc/passwd\n\n.. raw:: html\n\n   <script>alert(1)</script>\n\nSafe text.'))
        self.assertIn('Safe text',html)
        self.assertNotIn('root:',html)
        self.assertNotIn('<script',html)

    def test_markdown_table_after_paragraph(self):
        from .templatetags.fgtida import rich_text
        self.assertIn('<table>',str(rich_text('Introduction\n| A | B |\n|---|---|\n| x | y |')))


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False)
class GitHubWorkflowTests(TestCase):
    # Reuse the isolated native-model setup without inheriting its test cases.
    def setUp(self):
        native_tests.NativeDatatrackerTests.setUp(self)
        self.account=GitHubAccount.objects.create(organization='FG-TIDA')
        self.repo=GitHubRepository.objects.create(account=self.account,name='example',group=self.root,github_id=123,paths=['doc.md'])

    def document(self):
        document=Document(name='fg-tida-release-test',title='Versioned item',group=self.root,type_id='tida')
        core.save_document(document,core.actor_for(), 'Initial submission',data=b'Submitted content',filename='source.txt',state=State.objects.get(type_id='tida',slug='draft'))
        item=WorkItem.objects.create(document=document,repository=self.repo,short_name='example')
        return document,item

    def artifacts(self,tag='',sha='a'*40):
        from pypdf import PdfWriter
        stream=io.BytesIO()
        writer=PdfWriter()
        writer.add_blank_page(width=100,height=100)
        writer.write(stream)
        pdf=stream.getvalue()
        source=io.BytesIO()
        with tarfile.open(fileobj=source,mode='w:gz') as archive:
            item=tarfile.TarInfo('src/doc/index.rst')
            data=b'Title\n=====\n'
            item.size=len(data)
            archive.addfile(item,io.BytesIO(data))
        manifest={'schema':1,'repository':self.repo.full_name,'document':'fg-tida-release-test','commit':sha,'tag':tag,
            'pdf_sha256':hashlib.sha256(pdf).hexdigest(),'source_sha256':hashlib.sha256(source.getvalue()).hexdigest()}
        return manifest,pdf,source.getvalue()

    def test_working_build_has_no_numbered_revision(self):
        document,item=self.document()
        manifest,pdf,source=self.artifacts()
        self.assertEqual(receive_build(self.repo,manifest,pdf),('working','00'))
        document.refresh_from_db()
        self.assertEqual(document.rev,'00')
        self.assertEqual(document.docevent_set.filter(type='new_revision').count(),1)
        self.assertEqual(receive_build(self.repo,manifest,pdf),('unchanged','00'))
        self.assertFalse(WorkItemRelease.objects.exists())

    def test_xy_versions_timeline_dates_aliases_and_manual_major_upload(self):
        from .models import DocumentVersion
        from .versioning import public_version
        document,item=self.document()
        self.assertEqual(public_version(document),'0.0')
        first=DocumentVersion.objects.get(document=document,revision='00').recorded
        core.save_document(document,core.actor_for(),'Major revision',data=b'Major revision',filename='major.txt',version='1.0')
        core.save_document(document,core.actor_for(),'Minor revision',data=b'Minor revision',filename='minor.txt')
        self.assertEqual(public_version(document),'1.1')
        self.assertEqual(DocumentVersion.objects.get(document=document,revision='00').recorded,first)
        response=self.client.get('/doc/'+document.name+'/revision/1.0/')
        self.assertContains(response,'fg-version-amber')
        self.assertContains(response,'fg-version-cyan')
        self.assertContains(response,'aria-current="page"')
        self.assertContains(response,'FG-TIDA workflow')
        self.assertNotContains(response,'No value found')
        self.assertEqual(response.context['snapshot'].rev,'01')
        self.assertEqual(self.client.get('/doc/'+document.name+'/revision/00/').status_code,200)
        self.assertEqual(self.client.get('/doc/'+document.name+'/download/1.0/').status_code,200)
        with self.assertRaises(ValidationError):
            core.save_document(document,core.actor_for(),'Replace forbidden',data=b'No',filename='no.txt',version='1.0')

    def test_release_label_matches_tag_and_rejects_reused_initial_label(self):
        from .versioning import public_version
        document,item=self.document()
        manifest,pdf,source=self.artifacts('v0.0')
        with self.assertRaises(ValidationError):
            receive_build(self.repo,manifest,pdf,source)
        receive_build(self.repo,{**manifest,'tag':'v0.1'},pdf,source)
        document.refresh_from_db()
        self.assertEqual(public_version(document),'0.1')
        receive_build(self.repo,{**manifest,'tag':'v1.0'},pdf,source)
        document.refresh_from_db()
        document.__dict__.pop('_version_labels',None)
        self.assertEqual(public_version(document),'1.0')

    def test_release_immutable_idempotent_and_increasing(self):
        document,item=self.document()
        manifest,pdf,source=self.artifacts('v1.0')
        self.assertEqual(receive_build(self.repo,manifest,pdf,source),('released','01'))
        self.assertEqual(receive_build(self.repo,manifest,pdf,source),('unchanged','01'))
        with self.assertRaises(ValidationError):
            receive_build(self.repo,{**manifest,'commit':'b'*40},pdf,source)
        with self.assertRaises(ValidationError):
            receive_build(self.repo,{**manifest,'tag':'v0.9'},pdf,source)
        self.assertEqual(receive_build(self.repo,{**manifest,'tag':'v1.1'},pdf,source),('released','02'))
        self.assertEqual(list(item.releases.order_by('revision').values_list('revision',flat=True)),['01','02'])

    def test_manual_edits_and_deletions_are_preserved(self):
        self.repo.mode='files'
        self.repo.save()
        args=(self.repo,'file:doc.md',b'# Original','doc.md','Original',self.repo.url+'/blob/main/doc.md','a'*40)
        store_source(*args)
        source=GitHubSource.objects.get(repository=self.repo)
        document=source.document
        preserve_manual_document(document)
        self.assertEqual(store_source(*args),'manual/excluded')
        document.delete()
        source.refresh_from_db()
        self.assertIsNone(source.document_id)
        self.assertEqual(source.sync_mode,'excluded')
        self.assertEqual(store_source(*args),'manual/excluded')
        self.assertFalse(Document.objects.filter(title='Original').exists())

    def test_sync_is_idempotent_and_dry_run_does_not_write(self):
        self.repo.mode='files'
        self.repo.save()
        args=(self.repo,'file:doc.md',b'# Original','doc.md','Original',self.repo.url+'/blob/main/doc.md','a'*40)
        self.assertEqual(store_source(*args,dry_run=True),'create')
        self.assertFalse(GitHubSource.objects.exists())
        store_source(*args)
        self.assertEqual(store_source(*args),'unchanged')
        self.assertEqual(Document.objects.get(title='Original').rev,'00')

    def test_legacy_work_item_without_file_gets_initial_archive(self):
        document=Document(name='fg-tida-link-only',title='Old link',rev='00',group=self.root,type_id='tida',external_url=self.repo.url+'/blob/main/doc.md')
        core.save_document(document,core.actor_for(),'Legacy external reference only')
        response=self.client.get('/doc/'+document.name+'/')
        self.assertContains(response,'No archived version')
        self.assertNotContains(response,'aria-label="Document versions"')
        args=(self.repo,'file:doc.md',b'# Full proposal\n\nComplete proposal body.','doc.md','Full proposal',document.external_url,'a'*40)
        store_source(*args)
        document.refresh_from_db()
        self.assertEqual(document.rev,'01')
        self.assertTrue(document.uploaded_filename)
        response=self.client.get('/doc/'+document.name+'/')
        self.assertContains(response,'Complete proposal body.')
        self.assertContains(response,'aria-label="Document content"')
        self.assertEqual(len(response.context['revisions']),1)
        self.assertEqual(response.context['revisions'][0].public_version,'0.0')
        self.assertNotContains(response,'No document content has been imported')
        store_source(self.repo,'file:doc.md',b'# New working draft','doc.md','New working draft',document.external_url,'b'*40)
        document.refresh_from_db()
        self.assertEqual(document.rev,'01')  # Main changes still cannot replace an archive.

    def test_imported_charter_is_linked_and_full_content_is_visible(self):
        from ietf.group.models import Group
        self.repo.mode='files'
        self.repo.document_type='charter'
        self.repo.group=Group.objects.get(acronym='tidawg1')
        self.repo.save()
        store_source(self.repo,'file:charter.md',b'# WG charter\n\nFull charter text.','charter.md','WG charter',self.repo.url+'/blob/main/charter.md','a'*40)
        self.repo.group.refresh_from_db()
        doc=self.repo.group.charter
        self.assertIsNotNone(doc)
        self.assertContains(self.client.get('/group/tidawg1/about/'),'Charter')
        response=self.client.get('/doc/'+doc.name+'/')
        self.assertContains(response,'Full charter text.')
        self.assertContains(response,'aria-label="Document content"')

    def test_content_audit_distinguishes_missing_files_from_missing_imports(self):
        document,item=self.document()
        output=io.StringIO()
        call_command('audit_fgtida_content',skip_checks=True,stdout=output)
        self.assertIn('"missing_current_version_label": 0',output.getvalue())
        self.assertIn('"missing_current_file_on_disk": 0',output.getvalue())
        self.assertNotIn(self.user.email,output.getvalue())
        Path(document.get_file_name()).unlink()
        output=io.StringIO()
        call_command('audit_fgtida_content',skip_checks=True,stdout=output)
        self.assertIn('"missing_current_file_on_disk": 1',output.getvalue())

    def test_webhook_imports_reject_private_or_replaced_repositories(self):
        from unittest.mock import Mock
        from .github_catalog import sync_repository
        self.repo.mode='issues'
        client=Mock()
        for metadata in [{'id':123,'private':True},{'id':999,'private':False}]:
            client.request.return_value=metadata
            with self.assertRaises(ValidationError):
                sync_repository(self.repo,client=client)
        client.pages.assert_not_called()

    def test_build_authentication_precedes_upload_parsing(self):
        self.document()
        with override_settings(FGTIDA_GITHUB_INGEST_ENABLED=True):
            self.assertEqual(self.client.post('/integrations/github/build/',{}).status_code,401)
            with patch('ietf.fgtida.github_views.verify_build_identity',side_effect=ValidationError('Invalid identity')):
                response=self.client.post('/integrations/github/build/',b'not multipart',content_type='multipart/form-data; boundary=x',HTTP_AUTHORIZATION='Bearer invalid',HTTP_X_FG_TIDA_REPOSITORY=self.repo.full_name)
                self.assertEqual(response.status_code,409)
        self.assertEqual(self.client.post('/integrations/github/build/',{}).status_code,503)

    def test_published_requires_archive_and_stops_new_builds(self):
        document,item=self.document()
        transition(document,'review',core.actor_for(),'Ready for review')
        with self.assertRaises(ValidationError):
            transition(document,'published',core.actor_for(),'Publish')
        manifest,pdf,source=self.artifacts('v1.0')
        receive_build(self.repo,manifest,pdf,source)
        transition(document,'published',core.actor_for(),'Approved publication')
        with self.assertRaises(ValidationError):
            receive_build(self.repo,{**manifest,'tag':'v1.1'},pdf,source)

    def test_incubation_adoption_keeps_original_notification_target(self):
        from .models import Contribution
        from .contributions import accept
        from ietf.group.models import Group
        incubation=Group.objects.get(acronym='incubation')
        item=Contribution.objects.create(submitter=self.user,group=incubation,title='New WG proposal',filename='proposal.txt',content=b'Original proposal')
        with self.assertRaises(ValidationError):
            accept(item.pk,self.user)
        group=Group.objects.get(acronym='tidawg2')
        accept(item.pk,self.user,group)
        item.refresh_from_db()
        self.assertEqual(item.group,incubation)
        self.assertEqual(item.document.group,group)
        group.refresh_from_db()
        self.assertIsNotNone(group.charter_id)
        self.assertEqual(GitHubJob.objects.filter(kind='provision').count(),2)

    def test_github_writes_are_disabled_without_opt_in(self):
        from .github_jobs import run_jobs
        document,item=self.document()
        job=GitHubJob.objects.create(kind='provision',work_item=item,key='disabled-test')
        with override_settings(FGTIDA_GITHUB_WRITE_ENABLED=False),patch('ietf.fgtida.github_jobs.provision') as provision:
            self.assertEqual(run_jobs(),0)
            provision.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.attempts,0)

    def test_email_confirmation_expiry_and_password_change_invalidate_links(self):
        from django.core import signing
        from .accounts import email_proof
        from .models import EmailVerification
        from django.urls import reverse
        self.user.is_active=False
        self.user.save(update_fields=['is_active'])
        EmailVerification.objects.create(user=self.user,email=self.user.email)
        payload=[self.user.pk,self.user.email,email_proof(self.user,self.user.email)]
        with patch('django.core.signing.time.time',return_value=0):
            expired=signing.dumps(payload,salt='fgtida-email-confirmation')
        self.assertEqual(self.client.post(reverse('fgtida:email-confirm',args=[expired])).status_code,404)
        token=signing.dumps(payload,salt='fgtida-email-confirmation')
        self.user.set_password('Changed!TestPassword99')
        self.user.save(update_fields=['password'])
        self.assertEqual(self.client.post(reverse('fgtida:email-confirm',args=[token])).status_code,404)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_webhook_signature_and_duplicate_delivery(self):
        import hmac,uuid
        self.repo.mode='issues'
        self.repo.save()
        payload=json.dumps({'repository':{'id':123,'full_name':self.repo.full_name},'ref':'refs/heads/main'}).encode()
        delivery=str(uuid.uuid4())
        headers={'HTTP_X_GITHUB_DELIVERY':delivery,'HTTP_X_GITHUB_EVENT':'push'}
        with override_settings(FGTIDA_GITHUB_WEBHOOK_SECRET='temporary-test-secret'):
            self.assertEqual(self.client.post('/integrations/github/webhook/',payload,content_type='application/json',**headers).status_code,401)
            headers['HTTP_X_HUB_SIGNATURE_256']='sha256='+hmac.new(b'temporary-test-secret',payload,hashlib.sha256).hexdigest()
            for _ in range(2):
                self.assertEqual(self.client.post('/integrations/github/webhook/',payload,content_type='application/json',**headers).status_code,200)
        self.assertEqual(GitHubJob.objects.filter(kind='catalog').count(),1)

    def test_administrators_can_manage_all_integration_records(self):
        for model in ['githubaccount','workinggroupconfiguration','githubrepository','githubsource','workitem','workitemrelease','githubjob','emailverification']:
            self.assertEqual(self.client.get('/admin/fgtida/'+model+'/').status_code,200,model)

    def test_topic_configuration_and_search_render_with_numbered_groups(self):
        call_command('configure_fgtida_github',skip_checks=True,stdout=io.StringIO())
        self.assertContains(self.client.get('/doc/'),'Use Cases and Terminology (wg0)')
        from django.urls import reverse
        self.assertContains(self.client.get(reverse('fgtida:groups')),'wg2')
