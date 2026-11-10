from datetime import timedelta
from io import StringIO
from pathlib import Path
import tempfile
import json
import base64

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from ietf.doc.models import Document, State
from ietf.group.models import Group, Role
from ietf.person.models import Person, Email
from ietf.meeting.models import Meeting, MeetingCategory, Session, Schedule, TimeSlot, SchedTimeSessAssignment, SessionPresentation, ImportantDate
from . import core


class PublicationSecurityTests(SimpleTestCase):
    def test_production_can_prepare_without_smtp_credentials(self):
        import os
        import runpy
        import secrets
        from unittest.mock import patch
        from django.core.exceptions import ImproperlyConfigured
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key'
            key.write_text(secrets.token_urlsafe(64))
            env = {'FGTIDA_SECRET_KEY_FILE': str(key), 'FGTIDA_DB_PASSWORD_FILE': str(key),
                   'FGTIDA_ORIGIN': 'https://tracker.example.test',
                   'FGTIDA_APPROVED_PRIVILEGED_USERS': 'admin-example',
                   'FGTIDA_DB_HOST': 'db', 'FGTIDA_DB_NAME': 'fgtida', 'FGTIDA_DB_USER': 'fgtida',
                   'FGTIDA_DATA_DIR': directory, 'FGTIDA_CELERY_BROKER_URL': 'redis://queue:6379/0'}
            with patch.dict(os.environ, env, clear=True):
                config = runpy.run_path(str(Path(__file__).with_name('production_settings.py')))
                self.assertFalse(config['FGTIDA_MAIL_ENABLED'])
                self.assertFalse(config['FGTIDA_MAIL_IMMEDIATE'])
                self.assertEqual(config['EMAIL_BACKEND'], 'django.core.mail.backends.dummy.EmailBackend')
                self.assertFalse(config['DEBUG'])
                os.environ['FGTIDA_MAIL_ENABLED'] = 'true'
                with self.assertRaises(ImproperlyConfigured):
                    runpy.run_path(str(Path(__file__).with_name('production_settings.py')))

    def test_public_text_and_links_do_not_access_database(self):
        from django.test import RequestFactory
        from .content import CONTENT, get_content, public_content
        # SimpleTestCase rejects database queries, guarding the per-page cost.
        request = RequestFactory().get('/')
        self.assertIs(get_content(request), CONTENT)
        self.assertIs(public_content(request)['platform_content'], CONTENT)
        self.assertEqual(CONTENT.secretariat_email, 'tsbfgtida@itu.int')

    def test_markdown_keeps_tables_and_removes_executable_html(self):
        from .templatetags.fgtida import rich_text
        html = str(rich_text('| A | B |\n|---|---|\n| 1 | 2 |\n\n<script>alert(1)</script><img src=x onerror=alert(1)>'))
        self.assertIn('<table>', html)
        self.assertNotIn('<script', html)
        self.assertNotIn('onerror=', html)

    def test_xml_external_entities_are_rejected(self):
        from .files import extract_text
        from django.core.exceptions import ValidationError
        payload = b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>'
        with self.assertRaises(ValidationError):
            extract_text(payload, 'untrusted.xml')

    def test_office_expansion_limit_is_enforced(self):
        import io
        import zipfile
        from .files import extract_text
        from django.core.exceptions import ValidationError
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('word/document.xml', b'<document>' + b'x' * (31 * 1024 * 1024) + b'</document>')
        with self.assertRaises(ValidationError):
            extract_text(data.getvalue(), 'untrusted.docx')


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False)
class NativeDatatrackerTests(TestCase):
    @override_settings(DEBUG=True, SERVER_MODE='development', FGTIDA_REQUIRE_ADMIN_MFA=True,
        FGTIDA_DEV_ADMIN_MFA_PAUSED=True)
    def test_admin_mfa_development_pause_preserves_devices_and_production_gate(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice
        TOTPDevice.objects.create(user=self.user, confirmed=True, name='Existing authenticator')
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertRedirects(self.client.get('/accounts/mfa/setup/'), '/', fetch_redirect_response=False)
        self.assertRedirects(self.client.get('/accounts/mfa/verify/'), '/', fetch_redirect_response=False)
        self.assertTrue(TOTPDevice.objects.filter(user=self.user, confirmed=True).exists())
        with override_settings(DEBUG=False, SERVER_MODE='production'):
            self.assertRedirects(self.client.get('/admin/'), '/accounts/mfa/verify/', fetch_redirect_response=False)
        TOTPDevice.objects.filter(user=self.user).delete()
        with override_settings(DEBUG=False, SERVER_MODE='production'):
            self.assertRedirects(self.client.get('/admin/'), '/accounts/mfa/setup/', fetch_redirect_response=False)
        member = get_user_model().objects.create_user('member-with-mfa', password='test-password')
        TOTPDevice.objects.create(user=member, confirmed=True)
        self.client.force_login(member)
        self.assertRedirects(self.client.get('/'), '/accounts/mfa/verify/', fetch_redirect_response=False)

    def test_static_public_content_and_registration(self):
        from .content import CONTENT
        home = self.client.get('/')
        for text in (CONTENT.home_title, CONTENT.community_title, CONTENT.community_url,
                     'mailto:' + CONTENT.secretariat_email, CONTENT.bug_report_url):
            self.assertContains(home, text)
        self.assertRedirects(self.client.get('/meeting/registration/'), CONTENT.registration_url, fetch_redirect_response=False)
        self.assertEqual(self.client.get('/admin/fgtida/platformcontent/').status_code, 404)

    @override_settings(FGTIDA_REQUIRE_ADMIN_MFA=True)
    def test_admin_mfa_enrollment_gate_replay_and_one_time_recovery(self):
        from django.test import Client
        from django_otp.oath import totp
        from django_otp.plugins.otp_totp.models import TOTPDevice
        from .models import MfaRecoveryCode
        self.assertRedirects(self.client.get('/admin/'), '/accounts/mfa/setup/', fetch_redirect_response=False)
        self.client.post('/accounts/mfa/setup/', {'begin': '1'})
        device = TOTPDevice.objects.get(user=self.user, confirmed=False)
        token = str(totp(device.bin_key)).zfill(6)
        response = self.client.post('/accounts/mfa/setup/', {'token':token, 'current_password':'test-password'})
        self.assertEqual(response.status_code, 200)
        codes = response.context['codes']
        self.assertEqual(len(codes), 10)
        self.assertNotIn(codes[0], str(list(MfaRecoveryCode.objects.values_list('digest', flat=True))))
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        other = Client()
        other.force_login(self.user)
        self.assertRedirects(other.get('/admin/'), '/accounts/mfa/verify/', fetch_redirect_response=False)
        replay = other.post('/accounts/mfa/verify/', {'token':token})
        self.assertContains(replay, 'Invalid or recently used code')
        self.assertEqual(other.post('/accounts/mfa/verify/', {'token':codes[0]}).status_code, 302)
        self.assertEqual(other.get('/admin/').status_code, 200)
        third = Client()
        third.force_login(self.user)
        self.assertContains(third.post('/accounts/mfa/verify/', {'token':codes[0]}), 'Invalid or recently used code')
        self.assertContains(other.post('/accounts/mfa/', {'action':'disable', 'token':codes[1], 'current_password':'test-password'}), 'must keep two-factor authentication enabled')
        self.assertTrue(TOTPDevice.objects.filter(user=self.user, confirmed=True).exists())

    @override_settings(FGTIDA_REQUIRE_ADMIN_MFA=True)
    def test_admin_mfa_code_returns_to_admin_despite_background_requests(self):
        from django.test import Client
        from django_otp.oath import totp
        from django_otp.plugins.otp_totp.models import TOTPDevice
        browser = Client()
        device = TOTPDevice.objects.create(user=self.user, confirmed=True)
        response = browser.post('/accounts/login/?next=/admin/',
            {'username': self.user.username, 'password': 'test-password'})
        self.assertRedirects(response, '/admin/', fetch_redirect_response=False)
        self.assertRedirects(browser.get('/admin/', HTTP_SEC_FETCH_DEST='document'),
            '/accounts/mfa/verify/', fetch_redirect_response=False)
        verification = browser.get('/accounts/mfa/verify/')
        self.assertNotContains(verification, 'data-component="Status"')
        self.assertNotContains(verification, 'data-group-menu-data-url')
        from django.urls import reverse
        for path, headers in [(reverse('ietf.group.views.group_menu_data'), {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}),
                              ('/status/latest.json', {'HTTP_SEC_FETCH_DEST': 'empty'}),
                              ('/favicon.ico', {'HTTP_SEC_FETCH_DEST': 'image'}),
                              ('/doc/', {'HTTP_SEC_FETCH_DEST': 'empty'}),
                              ('/accounts/review/', {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}),
                              ('/not-a-tracker-page/', {'HTTP_SEC_FETCH_DEST': 'document'})]:
            browser.get(path, **headers)
            self.assertEqual(browser.session['fgtida_mfa_next'], '/admin/')
        response = browser.post('/accounts/mfa/verify/', {'token': str(totp(device.bin_key)).zfill(6)})
        self.assertRedirects(response, '/admin/')
        self.assertNotIn('fgtida_mfa_next', browser.session)

    def test_mfa_stale_missing_hidden_and_unsafe_destinations_fall_back_home(self):
        from django.test import RequestFactory
        from .mfa import destination
        request = RequestFactory().get('/accounts/mfa/verify/')
        request.user = self.user
        for target in ['/api/', '/accounts/apikey/', '/accounts/review/', '/admin/nomcom/',
                       '/favicon.ico', '/not-a-tracker-page/', '/accounts/mfa/verify/',
                       '/accounts/login/', 'admin/', 'https://untrusted.example.invalid/',
                       '//untrusted.example.invalid/', None]:
            request.session = {'fgtida_mfa_next': target}
            self.assertEqual(destination(request), '/', target)
            self.assertNotIn('fgtida_mfa_next', request.session)
        for target in ['/admin/', '/doc/?q=tida', '/accounts/profile/']:
            request.session = {'fgtida_mfa_next': target}
            self.assertEqual(destination(request), target)

    @override_settings(FGTIDA_REQUIRE_ADMIN_MFA=True)
    def test_regular_login_goes_home_and_mfa_is_optional(self):
        from django.test import Client
        user = get_user_model().objects.create_user(username='member-login-test', password='test-password')
        core.actor_for(user)
        browser = Client()
        response = browser.post('/accounts/login/', {'username':user.username, 'password':'test-password'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)
        self.assertEqual(browser.get('/').status_code, 200)
        self.assertContains(browser.get('/accounts/mfa/'), 'Begin authenticator setup')

    def test_security_question_catalog_is_editable_and_preserves_existing_prompts(self):
        from .accounts import QUESTIONS, QuestionsForm, question_label
        from .models import SecurityQuestion, RecoveryQuestions
        for order, (slug, prompt) in enumerate(QUESTIONS):
            SecurityQuestion.objects.get_or_create(slug=slug, defaults={'prompt':prompt, 'order':order})
        form = QuestionsForm({'question_1':'book', 'answer_1':'my private book', 'question_2':'place', 'answer_2':'my private place', 'question_3':'phrase', 'answer_3':'my private phrase'})
        self.assertTrue(form.is_valid(), form.errors)
        form.save(self.user)
        recovery = RecoveryQuestions.objects.get(user=self.user)
        old_prompt = question_label(recovery, 'book')
        response = self.client.post('/admin/fgtida/securityquestion/add/', {'slug':'custom-question', 'prompt':'What private recovery phrase did you choose?', 'active':'on', 'order':20, '_save':'Save'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('custom-question', dict(QuestionsForm().fields['question_1'].choices))
        SecurityQuestion.objects.filter(slug='book').update(prompt='Changed prompt', active=False)
        self.assertEqual(question_label(recovery, 'book'), old_prompt)
        self.assertNotIn('book', dict(QuestionsForm().fields['question_1'].choices))

    def test_meeting_materials_list_deduplicates_documents_and_includes_uploads(self):
        from .models import SessionResource
        meeting = Meeting.objects.create(number='materials-catalog-test', type_id='tida', date=timezone.localdate(), days=1, time_zone='UTC')
        sessions = [Session.objects.create(meeting=meeting, group=self.root, name=name, type_id='session', purpose_id='regular') for name in ('Opening', 'Discussion')]
        doc = Document.objects.create(name='agenda-materials-catalog-test', title='Shared meeting agenda', type_id='agenda', group=self.root)
        for session in sessions:
            SessionPresentation.objects.create(session=session, document=doc)
        SessionResource.objects.create(session=sessions[1], kind='notes', title='Discussion summary', url='https://example.test/notes')
        url = '/meeting/materials-catalog-test/materials/'
        response = self.client.get(url)
        self.assertContains(response, 'Shared meeting agenda', count=1)
        self.assertContains(response, 'Discussion summary', count=1)
        self.assertContains(response, 'Upload meeting document')
        self.assertContains(response, 'Upload file or recording')
        self.client.logout()
        response = self.client.get(url)
        self.assertContains(response, 'Shared meeting agenda', count=1)
        self.assertNotContains(response, 'Upload meeting document')

    def test_session_resource_actions_and_tar_download(self):
        import io
        import tarfile
        from .resource_files import SessionResourceForm
        from .models import SessionResource
        from .templatetags.fgtida import session_actions
        meeting = Meeting.objects.create(number='session-download-test', type_id='tida', date=timezone.localdate(), days=1, time_zone='UTC')
        session = Session.objects.create(meeting=meeting, group=self.root, name='Public session', type_id='session', purpose_id='regular')
        payload = b'Session summary\n' * 80000
        form = SessionResourceForm({'session':session.pk, 'kind':'notes', 'title':'Summary'},
            {'upload':SimpleUploadedFile('summary.txt', payload)})
        self.assertTrue(form.is_valid(), form.errors)
        resource = form.save()
        SessionResource.objects.create(session=session, kind='recording', title='Recording', url='https://example.test/recording')
        actions = {item['key']:item for item in session_actions(session)['actions']}
        self.assertEqual(actions['notes']['links'][0][1], resource.resource_url)
        self.assertFalse(actions['pdf']['links'])
        self.client.logout()
        response = self.client.get(f'/session/{session.pk}/materials.tar')
        self.assertEqual(response.status_code, 200)
        with tarfile.open(fileobj=io.BytesIO(b''.join(response.streaming_content))) as archive:
            self.assertEqual(archive.extractfile(f'resources/{resource.pk}-summary.txt').read(), payload)
            self.assertIn(b'https://example.test/recording', archive.extractfile('README.txt').read())
            self.assertEqual(len(archive.getmembers()), 2)
        self.assertEqual(self.client.post(f'/session/{session.pk}/materials.tar').status_code, 405)
        self.assertEqual(self.client.get('/session/99999999/materials.tar').status_code, 404)

    def test_full_meeting_title_keeps_stable_agenda_url(self):
        title = '1st ITU-T Focus Group on Trust and Identity for Humans and Agent'
        meeting = Meeting.objects.create(number='tida-title-test', type_id='tida', date=timezone.localdate(), days=1, time_zone='UTC')
        url = f'/admin/meeting/meeting/{meeting.pk}/change/'
        values = self.admin_payload(url, {'title': title, 'number': meeting.number, 'type': 'tida',
            'date': meeting.date.isoformat(), 'days': 1, 'time_zone': 'UTC', '_save': 'Save'})
        response = self.client.post(url, values)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['adminform'].form.errors)
        meeting.refresh_from_db()
        self.assertEqual(meeting.title, title)
        self.assertEqual(meeting.number, 'tida-title-test')
        self.assertEqual(str(meeting), title)
        self.assertContains(self.client.get('/meeting/tida-title-test/agenda/'), title)

    def test_meeting_admin_rejects_titles_as_url_identifiers(self):
        meeting = Meeting.objects.create(number='tida-admin-identifier', type_id='tida', date=timezone.localdate(), days=1, time_zone='UTC')
        url = f'/admin/meeting/meeting/{meeting.pk}/change/'
        values = self.admin_payload(url, {'number': '1st ITU-T Focus Group on Trust and Identity for Humans and Agent',
            'type': 'tida', 'date': meeting.date.isoformat(), 'days': 1, 'time_zone': 'UTC', '_save': 'Save'})
        response = self.client.post(url, values)
        self.assertEqual(response.status_code, 200)
        self.assertIn('number', response.context['adminform'].form.errors)
        meeting.refresh_from_db()
        self.assertEqual(meeting.number, 'tida-admin-identifier')
        self.assertContains(response, 'Meeting URL identifier')
        self.assertEqual(self.client.get('/meeting/tida-admin-identifier/agenda/').status_code, 200)

    def test_session_attachments_are_stored_in_postgres_and_seekable(self):
        from .models import SessionResource, SessionResourceChunk
        from .resource_files import CHUNK_SIZE
        meeting = Meeting.objects.create(number='attachment-test', type_id='tida', date=timezone.localdate(), days=1, time_zone='UTC')
        session = Session.objects.create(meeting=meeting, group=self.root, name='Files', type_id='session', purpose_id='regular')
        response = self.client.post('/admin/fgtida/sessionresource/add/', {'session': session.pk, 'kind': 'materials', 'title': 'Session notes', 'upload': SimpleUploadedFile('notes.txt', b'FG-TIDA session notes'), '_save': 'Save'})
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['adminform'].form.errors)
        resource = SessionResource.objects.get(title='Session notes')
        self.assertEqual(resource.url, '')
        self.assertEqual(bytes(resource.file_chunks.get().content), b'FG-TIDA session notes')
        self.client.logout()
        response = self.client.get(resource.resource_url)
        self.assertEqual(b''.join(response.streaming_content), b'FG-TIDA session notes')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.client.force_login(self.user)
        from .resource_files import SessionResourceForm
        data = b'\x00\x00\x00\x18ftypisom' + b'x' * (CHUNK_SIZE + 30)
        form = SessionResourceForm({'session': session.pk, 'kind': 'recording', 'title': 'Recording'}, {'upload': SimpleUploadedFile('recording.mp4', data)})
        self.assertTrue(form.is_valid(), form.errors)
        recording = form.save()
        self.assertEqual(recording.file_chunks.count(), 2)
        self.client.logout()
        start, end = CHUNK_SIZE - 7, CHUNK_SIZE + 8
        response = self.client.get(recording.resource_url, HTTP_RANGE=f'bytes={start}-{end}')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), data[start:end+1])
        self.assertEqual(response['Content-Range'], f'bytes {start}-{end}/{len(data)}')
        self.assertEqual(self.client.get(recording.resource_url, HTTP_RANGE='bytes=999999999-').status_code, 416)
        head = self.client.head(recording.resource_url)
        self.assertEqual(head['Content-Length'], str(len(data)))
        self.assertEqual(b''.join(head.streaming_content), b'')
        old_version = recording.file_version
        replacement = SessionResourceForm({'session': session.pk, 'kind': 'recording', 'title': 'Recording'}, {'upload': SimpleUploadedFile('new.mp4', data[:20])}, instance=recording)
        self.assertTrue(replacement.is_valid(), replacement.errors)
        replacement.save()
        recording.refresh_from_db()
        self.assertNotEqual(recording.file_version, old_version)
        self.assertEqual(recording.file_chunks.count(), 1)
        self.assertEqual(SessionResourceChunk.objects.filter(resource=recording).count(), 1)

    def test_session_attachment_validation_and_inline_upload(self):
        from .resource_files import SessionResourceForm, MEDIA_LIMIT
        from .models import SessionResource
        meeting = Meeting.objects.create(number='inline-file', type_id='tida', date=timezone.localdate(), days=1, time_zone='UTC')
        base = {'kind': 'recording', 'title': 'Invalid'}
        form = SessionResourceForm(base, {'upload': SimpleUploadedFile('bad.mp4', b'<script>bad</script>')})
        self.assertFalse(form.is_valid())
        self.assertIn('upload', form.errors)
        large = SimpleUploadedFile('large.mp4', b'\x00\x00\x00\x18ftypisom')
        large.size = MEDIA_LIMIT + 1
        form = SessionResourceForm(base, {'upload': large})
        self.assertFalse(form.is_valid())
        self.assertIn('upload', form.errors)
        values = self.admin_payload('/admin/meeting/session/add/', {'meeting': meeting.pk, 'name': 'Inline file', 'group': self.root.pk, 'purpose': 'regular', 'type': 'session', '_save': 'Save'})
        values.update({'tida_resources-TOTAL_FORMS': '1', 'tida_resources-0-kind': 'materials', 'tida_resources-0-title': 'Materials', 'tida_resources-0-upload': SimpleUploadedFile('materials.txt', b'Inline database attachment')})
        response = self.client.post('/admin/meeting/session/add/', values)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['adminform'].form.errors)
        resource = SessionResource.objects.get(title='Materials')
        self.assertEqual(bytes(resource.file_chunks.get().content), b'Inline database attachment')

    def test_login_refreshes_token_after_another_tab_signs_in(self):
        from django.test import Client
        browser = Client(enforce_csrf_checks=True)
        old_token = browser.get('/accounts/csrf/').json()['csrfToken']
        credentials = {'username': self.user.username, 'password': 'test-password', 'csrfmiddlewaretoken': old_token}
        self.assertEqual(browser.post('/accounts/login/', credentials).status_code, 302)
        self.assertEqual(browser.post('/accounts/login/', credentials).status_code, 403)
        refreshed = browser.get('/accounts/csrf/')
        self.assertIn('no-store', refreshed['Cache-Control'])
        credentials['csrfmiddlewaretoken'] = refreshed.json()['csrfToken']
        self.assertEqual(browser.post('/accounts/login/', credentials).status_code, 302)
        self.assertEqual(int(browser.session['_auth_user_id']), self.user.pk)
        self.assertEqual(browser.post('/accounts/login/', credentials, HTTP_ORIGIN='https://untrusted.example.invalid').status_code, 403)

    def test_all_content_admin_lists_and_creation_forms_are_accessible(self):
        import re
        from django.contrib import admin
        from django.apps import apps
        from django.test import RequestFactory
        from django.urls import reverse
        from .security import ADMIN_MODELS
        request = RequestFactory().get('/admin/')
        request.user = self.user
        Email.objects.get_or_create(address=self.user.email, defaults={'person': core.actor_for(self.user), 'origin': 'test fixture'})
        for app, names in ADMIN_MODELS.items():
            for name in sorted(names):
                with self.subTest(model=f'{app}.{name}'):
                    model = apps.get_model(app, name)
                    self.assertIn(model, admin.site._registry)
                    self.assertEqual(self.client.get(f'/admin/{app}/{name}/').status_code, 200)
                    record = admin.site._registry[model].get_queryset(request).first()
                    if model == Email:
                        record = Email.objects.get(address=self.user.email)
                    if record is not None:
                        from django.contrib.admin.utils import quote
                        response = self.client.get(reverse(f'admin:{app}_{name}_change', args=[quote(record.pk)]))
                        self.assertEqual(response.status_code, 200, getattr(response, 'url', ''))
                    can_add = admin.site._registry[model].has_add_permission(request)
                    response = self.client.get(f'/admin/{app}/{name}/add/')
                    self.assertEqual(response.status_code, 200 if can_add else 403)
                    if can_add:
                        for related_app, related_model in re.findall(r'href="/admin/([^/]+)/([^/]+)/add/\?', response.content.decode()):
                            self.assertIn(related_model, ADMIN_MODELS.get(related_app, set()))

    def test_admin_relationship_search_is_paginated_and_permission_checked(self):
        from django.contrib import admin
        Meeting.objects.bulk_create([Meeting(number=f'admin-scale-{n:03}', type_id='tida', date=timezone.localdate(), days=1, time_zone='UTC') for n in range(55)])
        args = {'app_label': 'meeting', 'model_name': 'session', 'field_name': 'meeting', 'term': 'admin-scale'}
        first = self.client.get('/admin/autocomplete/', args)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.json()['results']), 20)
        self.assertTrue(first.json()['pagination']['more'])
        second = self.client.get('/admin/autocomplete/', {**args, 'page': 2}).json()
        self.assertFalse({r['id'] for r in first.json()['results']} & {r['id'] for r in second['results']})
        self.assertEqual(self.client.get('/admin/autocomplete/', {**args, 'app_label': 'iesg', 'model_name': 'telechatdate'}).status_code, 404)
        for model_admin in admin.site._registry.values():
            from .security import ADMIN_MODELS
            opts = model_admin.model._meta
            if opts.model_name in ADMIN_MODELS.get(opts.app_label, set()):
                self.assertEqual(model_admin.list_per_page, 50)
                self.assertFalse(model_admin.show_full_result_count)
        self.client.logout()
        self.assertEqual(self.client.get('/admin/autocomplete/', args).status_code, 302)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        override = override_settings(DOCUMENT_PATH_PATTERN=temporary.name + '/{doc.type_id}/',
                                     AGENDA_PATH=temporary.name + '/meetings/', MEDIA_ROOT=temporary.name + '/media/', ENABLE_BLOBSTORAGE=False)
        override.enable()
        self.addCleanup(override.disable)
        call_command('configure_fgtida', skip_checks=True, stdout=StringIO())
        self.root = Group.objects.get(acronym='tida')
        self.user = get_user_model().objects.create_superuser(username='fg-admin', email='admin@example.test', password='test-password')
        core.actor_for(self.user)
        self.client.force_login(self.user)

    def admin_payload(self, url, values):
        if '/doc/document/' in url:
            values.setdefault('catalogued', 'on')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        for inline in response.context['inline_admin_formsets']:
            prefix = inline.formset.prefix
            values.update({prefix + '-TOTAL_FORMS': '0', prefix + '-INITIAL_FORMS': '0',
                           prefix + '-MIN_NUM_FORMS': '0', prefix + '-MAX_NUM_FORMS': '1000'})
        return values

    def create_doc(self):
        state = State.objects.get(type_id='tida', slug='submitted')
        values = self.admin_payload('/admin/doc/document/add/', {
            'name': 'fg-tida-native-test', 'title': 'Native identity document', 'type': 'tida',
            'group': self.root.pk, 'abstract': 'Identity management requirements',
            'lifecycle_state': state.pk, 'comment_about_changes': 'Initial admin submission',
            'upload': SimpleUploadedFile('identity.txt', b'Native Datatracker document content'), '_save': 'Save',
        })
        response = self.client.post('/admin/doc/document/add/', values)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['adminform'].form.errors)
        return Document.objects.get(name='fg-tida-native-test')

    def test_contribution_has_public_submitted_record_before_adoption(self):
        from .models import Contribution
        from .contributions import accept
        member = get_user_model().objects.create_user('contributor', password='test-password')
        self.client.force_login(member)
        response = self.client.post('/submit/', {'title': 'Community proposal', 'group': Group.objects.get(acronym='tidawg1').pk,
            'abstract': 'Proposal summary', 'upload': SimpleUploadedFile('proposal.txt', b'Contribution content')})
        self.assertEqual(response.status_code, 302)
        item = Contribution.objects.get()
        self.assertTrue(Document.objects.filter(title=item.title).exists())
        self.assertEqual(item.document.rev, '00')
        self.assertEqual(item.document.states.get(type_id='tida').slug, 'submitted')
        stranger = get_user_model().objects.create_user('stranger', password='test-password')
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(f'/submit/{item.pk}/download/').status_code, 404)
        accept(item.pk, self.user)
        accept(item.pk, self.user)
        item.refresh_from_db()
        self.assertEqual(item.status, 'accepted')
        self.assertEqual(Document.objects.filter(title=item.title).count(), 1)
        self.assertEqual(self.client.get(f'/doc/{item.document.name}/').status_code, 200)

    def test_contribution_requires_login_and_valid_file(self):
        self.client.logout()
        self.assertEqual(self.client.get('/submit/').status_code, 302)
        self.client.force_login(self.user)
        self.assertContains(self.client.post('/submit/', {'title': 'Bad file', 'group': Group.objects.get(acronym='tidawg1').pk,
            'upload': SimpleUploadedFile('file.exe', b'bad')}), 'Use a PDF')

    def test_admin_user_creation_and_profile(self):
        response = self.client.get('/admin/auth/user/add/')
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/admin/auth/user/add/', {'username': 'new-member',
            'password1': 'Harbor!Quartz$River77-cloud', 'password2': 'Harbor!Quartz$River77-cloud',
            'usable_password': 'true', '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        created = get_user_model().objects.get(username='new-member')
        self.assertTrue(Person.objects.filter(user=created).exists())
        self.client.force_login(created)
        self.assertContains(self.client.get('/'), 'Public profile page')
        self.assertEqual(self.client.get(f'/people/{created.person.pk}/').status_code, 200)
        self.assertNotContains(self.client.get('/'), 'API keys')
        for path in ('/accounts/apikey/', '/accounts/apikey/add/', '/accounts/apikey/del/',
                     '/api/', '/api/schema/', '/api/red/doc/'):
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.client.post(path, {}).status_code, 404)
        self.assertEqual(self.client.post('/accounts/logout/').status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_session_resource_links_and_validation(self):
        from .models import SessionResource
        from django.template.loader import render_to_string
        from django.core.exceptions import ValidationError
        meeting = Meeting.objects.create(number='tida-icons', type_id='tida', date=timezone.now().date(), days=1)
        session = Session.objects.create(meeting=meeting, group=self.root, name='Review session', type_id='session', purpose_id='regular')
        for kind in ['notes', 'wiki', 'recording', 'materials', 'login']:
            SessionResource.objects.create(session=session, kind=kind, title=kind, url=f'https://example.test/{kind}')
        session.current_status = 'sched'
        html = render_to_string('fgtida/session_links.html', {'session': session})
        for icon in ['headset', 'camera-video', 'file-earmark-pdf', 'book', 'chat-left-text']:
            self.assertIn('bi-' + icon, html)
        session.current_status = 'canceled'
        self.assertNotIn('https://example.test/login', render_to_string('fgtida/session_links.html', {'session': session}))
        with self.assertRaises(ValidationError):
            SessionResource(session=session, kind='notes', title='Unsafe', url='javascript:alert(1)').full_clean()

    @override_settings(DEBUG=True)
    def test_database_console_requires_superuser(self):
        self.assertEqual(self.client.get('/admin-tools/database-access/').status_code, 204)
        self.client.force_login(get_user_model().objects.create_user('db-outsider'))
        self.assertEqual(self.client.get('/admin-tools/database-access/').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get('/admin-tools/database-access/').status_code, 401)

    def test_publication_boundary_blocks_inherited_interfaces(self):
        self.client.logout()
        for path in ['/api/v1/doc/document/?limit=1', '/api/schema/', '/feed/', '/iesg/',
                     '/accounts/apikey/', '/accounts/apikey/add/', '/accounts/apikey/del/', '/admin/meeting/schedulingevent/',
                     '/doc/rfc9110/history/', '/person/1/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get('/meeting/').status_code, 200)
        self.assertRedirects(self.client.get('/meeting/upcoming?year=2026'),
                             '/meeting/upcoming/?year=2026', status_code=301)
        for path in ['/accounts/login/', '/accounts/create/', '/accounts/reset/']:
            self.assertEqual(self.client.get(path).status_code, 200)

    @override_settings(FGTIDA_MAIL_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_registration_email_and_three_question_reset(self):
        import re
        from urllib.parse import urlsplit
        from unittest.mock import patch
        from django.core import mail
        from .models import RecoveryQuestions
        self.client.logout()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post('/accounts/create/', {'email': 'NewMember@Example.test'})
            self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)
        path = response.url
        detail = self.client.get(path)
        self.assertEqual(detail['Referrer-Policy'], 'same-origin')
        self.assertEqual(detail['Cache-Control'], 'no-store')
        password = 'Harbor!Quartz$River77-cloud'
        answers = {'question_1': 'book', 'answer_1': 'private book answer', 'question_2': 'place',
                   'answer_2': 'private place answer', 'question_3': ':custom:',
                   'custom_question_3': 'What is my imaginary lighthouse called?', 'answer_3': 'purple glass harbour'}
        short_password = 'H3!qV9$zT5!aJ'
        response = self.client.post(path, {**answers, 'name': 'New Member',
            'password': short_password, 'password_confirmation': short_password})
        self.assertContains(response, 'at least 15 characters')
        self.assertFalse(get_user_model().objects.filter(username='newmember@example.test').exists())
        Person.objects.create(name='New', ascii='New')
        Person.objects.create(name='New Member', ascii='New Member')
        with patch('ietf.person.models.send_mail_preformatted', side_effect=Person.DoesNotExist) as legacy_alert:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(path, {**answers, 'name': 'New Member', 'first_name': 'New',
                    'password': password, 'password_confirmation': password})
            legacy_alert.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item.subject for item in mail.outbox], ['Welcome - email confirmation'])
        message = mail.outbox[0]
        self.assertTrue(message.body.startswith('Dear New,'))
        (alternative,) = message.alternatives
        self.assertEqual(alternative.mimetype, 'text/html')
        self.assertIn('https://fg-tida.github.io/.github/', message.body)
        self.assertIn('https://fg-tida.github.io/.github/', alternative.content)
        self.assertIn('/meeting/', message.body)
        self.assertIn('/meeting/">Open Datatracker meetings', alternative.content)
        self.assertIn('<em>Please do not reply to this email.</em>', alternative.content)
        user = get_user_model().objects.get(username='newmember@example.test')
        self.assertEqual(user.person.name, 'New Member')
        self.assertFalse(user.person.alias_set.filter(name='New').exists())
        self.assertFalse(user.is_active)
        confirmation_path = urlsplit(re.search(r'https?://\S+', mail.outbox[-1].body).group()).path
        self.assertEqual(self.client.get(confirmation_path).status_code, 200)
        user.refresh_from_db()
        self.assertFalse(user.is_active)  # Link scanners cannot activate accounts.
        self.assertEqual(self.client.post(confirmation_path).status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        recovery = RecoveryQuestions.objects.get(user=user)
        self.assertEqual(recovery.prompts[recovery.questions[2]], answers['custom_question_3'])
        self.assertFalse(user.is_superuser)
        self.assertNotIn('private book answer', str(recovery.answer_hashes))
        self.assertFalse(user.is_staff)
        self.assertTrue(user.check_password(password))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post('/accounts/reset/', {'email': user.email})
        reset_path = urlsplit(re.search(r'https?://\S+', mail.outbox[-1].body).group()).path
        self.assertContains(self.client.get(reset_path), answers['custom_question_3'])
        reset_data = {'password': 'Valley!Satellite82$amber', 'password_confirmation': 'Valley!Satellite82$amber',
            'answer_1': 'wrong answer', 'answer_2': answers['answer_2'], 'answer_3': answers['answer_3']}
        self.assertContains(self.client.post(reset_path, reset_data), 'answers did not match')
        user.refresh_from_db()
        self.assertTrue(user.check_password(password))
        reset_data['answer_1'] = answers['answer_1']
        with self.captureOnCommitCallbacks(execute=True):
            self.assertContains(self.client.post(reset_path, reset_data), 'Password reset complete')
        user.refresh_from_db()
        self.assertTrue(user.check_password(reset_data['password']))
        self.assertEqual(self.client.get(reset_path).status_code, 404)
        self.assertEqual(len(mail.outbox), 2)  # Welcome and reset request only.

    def test_account_changes_require_existing_security_answers(self):
        from .accounts import QuestionsForm
        answers = {'question_1': 'book', 'answer_1': 'private alpha', 'question_2': 'place', 'answer_2': 'private beta', 'question_3': 'phrase', 'answer_3': 'private gamma'}
        enrollment = QuestionsForm(answers)
        self.assertTrue(enrollment.is_valid())
        enrollment.save(self.user)
        data = {'name': 'Updated Member', 'biography': 'A new biography', 'current_password': 'test-password', **{k: v for k, v in answers.items() if k.startswith('answer_')}}
        bad = {**data, 'answer_1': 'incorrect answer'}
        self.assertContains(self.client.post('/accounts/profile/', bad), 'answers did not match')
        self.user.person.refresh_from_db()
        self.assertNotEqual(self.user.person.name, 'Updated Member')
        self.assertContains(self.client.post('/accounts/profile/', data), 'changes have been saved')
        self.user.person.refresh_from_db()
        self.assertEqual(self.user.person.name, 'Updated Member')
        password_data = {'old_password': 'test-password', 'new_password1': 'Quartz!River27$Private', 'new_password2': 'Quartz!River27$Private', **{k: v for k, v in answers.items() if k.startswith('answer_')}}
        self.assertContains(self.client.post('/accounts/password/', {**password_data, 'answer_3': 'wrong'}), 'answers did not match')
        self.assertContains(self.client.post('/accounts/password/', password_data), 'changes have been saved')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(password_data['new_password1']))

    def test_admin_adds_scheduled_agenda_in_one_form(self):
        meeting = Meeting.objects.create(number='simple-agenda', type_id='tida', date=timezone.localdate(), days=1, time_zone='Europe/Paris')
        values = self.admin_payload('/admin/meeting/session/add/', {'meeting': meeting.pk, 'name': 'Opening session', 'group': self.root.pk, 'purpose': 'regular', 'type': 'session', 'on_agenda': 'on', 'agenda_start': str(meeting.date) + 'T10:00', 'agenda_minutes': '60', '_save': 'Save'})
        response = self.client.post('/admin/meeting/session/add/', values)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['adminform'].form.errors)
        meeting.refresh_from_db()
        assignment = SchedTimeSessAssignment.objects.get(schedule_id=meeting.schedule_id)
        self.assertEqual(assignment.timeslot.duration, timedelta(hours=1))
        self.assertContains(self.client.get('/meeting/simple-agenda/agenda/'), 'Opening session')
        self.assertContains(self.client.get('/meeting/simple-agenda/agenda/'), '10:00')
        edit = self.client.get(f'/admin/meeting/session/{assignment.session_id}/change/')
        self.assertContains(edit, 'Upload agenda document')

    def test_reset_question_lockout(self):
        from django.core import signing
        from django.contrib.auth.tokens import default_token_generator
        from django.contrib.auth.hashers import make_password
        from .models import RecoveryQuestions
        recovery = RecoveryQuestions.objects.create(user=self.user, questions=['book', 'place', 'phrase'],
            answer_hashes=[make_password('answer one'), make_password('answer two'), make_password('answer three')], failures=4)
        token = signing.dumps([self.user.pk, default_token_generator.make_token(self.user)], salt='fgtida-reset')
        path = '/accounts/reset/confirm/' + token + '/'
        payload = {'password': 'Valley!Satellite82$amber', 'password_confirmation': 'Valley!Satellite82$amber',
                   'answer_1': 'bad answer', 'answer_2': 'bad answer', 'answer_3': 'bad answer'}
        self.client.post(path, payload)
        recovery.refresh_from_db()
        self.assertGreater(recovery.locked_until, timezone.now())
        self.assertContains(self.client.post(path, payload), 'Too many attempts')

    def test_superuser_can_create_another_administrator(self):
        from .models import AdministratorApproval
        payload = {'username': 'additional-admin', 'email': 'admin-two@example.test', 'is_staff': 'on', 'is_superuser': 'on',
            'password1': 'Harbor!Quartz$River77-cloud', 'password2': 'Harbor!Quartz$River77-cloud', 'usable_password': 'true', '_save': 'Save'}
        response = self.client.post('/admin/auth/user/add/', payload)
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username='additional-admin')
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertTrue(AdministratorApproval.objects.filter(user=user, approved_by=self.user).exists())

    @override_settings(FGTIDA_MAIL_ENABLED=False)
    def test_superuser_can_delete_an_account_without_deleting_its_contributions(self):
        import re
        from urllib.parse import urlsplit
        from django_otp.plugins.otp_totp.models import TOTPDevice
        from ietf.doc.models import DocumentAuthor
        from .models import AdministratorApproval, Contribution, EmailVerification, OutgoingMail, RecoveryQuestions
        member = get_user_model().objects.create_user('delete-member', email='delete-member@example.test')
        person = core.actor_for(member)
        document = Document.objects.create(name='delete-member-doc', title='Keep this document',
            group=self.root, type_id='tida')
        email = Email.objects.create(address=member.email, person=person, origin='FG-TIDA registration')
        author = DocumentAuthor.objects.create(document=document, person=person, email=email)
        item = Contribution.objects.create(submitter=member, group=self.root, document=document,
            title='Keep this contribution', filename='source.txt', content=b'Archive')
        approval = AdministratorApproval.objects.create(user=self.user, approved_by=member)
        queued = OutgoingMail.objects.create(recipient=member.email, subject='Reset', body='Test', purpose='reset')
        verification = EmailVerification.objects.create(user=member, email=member.email)
        TOTPDevice.objects.create(user=member, name='Authenticator', confirmed=True)
        RecoveryQuestions.objects.create(user=member)
        self.assertEqual(self.client.get(f'/admin/fgtida/emailverification/{verification.pk}/delete/').status_code, 403)
        self.assertEqual(self.client.post(f'/admin/auth/user/{self.user.pk}/delete/', {'post': 'yes'}).status_code, 403)
        preview = self.client.get(f'/admin/auth/user/{member.pk}/delete/')
        self.assertFalse(preview.context['perms_lacking'])
        response = self.client.post(f'/admin/auth/user/{member.pk}/delete/', {'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(get_user_model().objects.filter(pk=member.pk).exists())
        self.assertFalse(EmailVerification.objects.filter(pk=verification.pk).exists())
        self.assertFalse(TOTPDevice.objects.filter(user_id=member.pk).exists())
        self.assertFalse(RecoveryQuestions.objects.filter(user_id=member.pk).exists())
        person.refresh_from_db()
        item.refresh_from_db()
        approval.refresh_from_db()
        queued.refresh_from_db()
        self.assertIsNone(person.user_id)
        self.assertIsNone(item.submitter_id)
        self.assertIsNone(approval.approved_by_id)
        self.assertEqual(item.document_id, document.pk)
        self.assertLessEqual(queued.expires, timezone.now())

        self.client.logout()
        reserved = Email.objects.create(address='historical-contact@example.test', person=person, origin='author: archived-document')
        self.assertContains(self.client.post('/accounts/create/', {'email': reserved.address}), 'already registered or reserved')
        response = self.client.post('/accounts/create/', {'email': member.email.upper()})
        self.assertEqual(response.status_code, 302)
        password = 'Harbor!Quartz$River77-cloud'
        response = self.client.post(response.url, {'name': 'New Profile', 'password': password,
            'password_confirmation': password, 'question_1': 'book', 'answer_1': 'purple glass harbour',
            'question_2': 'place', 'answer_2': 'orange clay cloud', 'question_3': 'phrase', 'answer_3': 'silver linden path'})
        self.assertContains(response, 'awaiting email confirmation')
        replacement = get_user_model().objects.get(email=member.email)
        self.assertFalse(replacement.is_active)
        self.assertFalse(replacement.is_superuser)
        self.assertNotEqual(replacement.person.pk, person.pk)
        self.assertContains(self.client.post('/accounts/create/', {'email': member.email}), 'already registered or reserved')
        confirmation = OutgoingMail.objects.get(recipient=member.email, purpose='verification')
        path = urlsplit(re.search(r'https?://\S+', confirmation.body).group()).path
        self.assertContains(self.client.post(path), 'Email confirmed')
        replacement.username = 'replacement-member'
        replacement.save(update_fields=['username'])
        self.assertRedirects(self.client.post('/accounts/login/', {'username': member.email, 'password': password}),
            '/', fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), replacement.pk)
        author.refresh_from_db()
        self.assertEqual(author.person_id, person.pk)
        self.assertEqual(author.email.person_id, person.pk)

    @override_settings(FGTIDA_MAIL_ENABLED=True)
    def test_smtp_failure_is_diagnosed_without_storing_the_provider_response(self):
        from smtplib import SMTPAuthenticationError
        from unittest.mock import patch
        from .mail import deliver
        from .models import OutgoingMail
        item = OutgoingMail.objects.create(recipient='member@example.test', subject='Confirm', body='Confirm')
        with patch('ietf.fgtida.mail.EmailMessage.send', side_effect=SMTPAuthenticationError(535, b'private response')):
            self.assertFalse(deliver(item.pk))
        item.refresh_from_db()
        self.assertIsNone(item.sent)
        self.assertEqual(item.attempts, 1)
        self.assertIn('SMTP authentication failed (535)', item.last_error)
        self.assertNotIn('private response', item.last_error)

    @override_settings(FGTIDA_MAIL_ENABLED=False)
    def test_contribution_notification_persists_without_smtp(self):
        from .models import Contribution, ContributionRecipient, OutgoingMail
        wg = Group.objects.get(acronym='tidawg1')
        ContributionRecipient.objects.create(group=wg, email='wg1@example.test')
        self.client.post('/submit/', {'title': 'Queued proposal', 'group': wg.pk,
            'upload': SimpleUploadedFile('proposal.txt', b'Persistent proposal')})
        item = Contribution.objects.get(title='Queued proposal')
        self.assertEqual(bytes(item.content), b'Persistent proposal')
        notification = OutgoingMail.objects.get(recipient='wg1@example.test')
        self.assertIsNone(notification.sent)
        self.assertEqual(notification.contribution_id, item.pk)

    @override_settings(FGTIDA_MAIL_ENABLED=True, FGTIDA_MAIL_IMMEDIATE=False,
                       EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_production_outbox_is_deferred_and_prioritizes_valid_reset_mail(self):
        from unittest.mock import patch
        from .mail import enqueue
        from .models import OutgoingMail
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            normal = enqueue('member@example.test', 'Welcome', 'Welcome')
            urgent = enqueue('member@example.test', 'Reset', 'Reset', expires=timezone.now()+timedelta(minutes=10))
        self.assertEqual(callbacks, [])
        OutgoingMail.objects.create(recipient='expired@example.test',subject='Expired',body='Expired',
                                    expires=timezone.now()-timedelta(seconds=1))
        OutgoingMail.objects.create(recipient='failed@example.test',subject='Failed',body='Failed',attempts=5)
        with patch('ietf.fgtida.management.commands.deliver_fgtida_mail.deliver', return_value=True) as deliver:
            call_command('deliver_fgtida_mail', stdout=StringIO())
        self.assertEqual([call.args[0] for call in deliver.call_args_list], [urgent.pk, normal.pk])

    @override_settings(FGTIDA_MAIL_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_working_group_and_incubation_attachment_routing(self):
        from django.core import mail
        from .models import Contribution, ContributionRecipient, OutgoingMail
        from .contributions import ContributionForm, queue_notifications
        from .mail import deliver
        member = get_user_model().objects.create_user('proposal-member', password='test-password')
        self.client.force_login(member)
        self.assertEqual(set(ContributionForm().fields['group'].queryset.values_list('acronym', flat=True)),
                         {'tidawg0', 'tidawg1', 'tidawg2', 'incubation'})
        for acronym in ('tidawg0', 'tidawg1', 'tidawg2', 'incubation'):
            group = Group.objects.get(acronym=acronym)
            address = acronym + '@example.test'
            self.client.post('/submit/', {'title': acronym + ' proposal', 'group': group.pk,
                'upload': SimpleUploadedFile('proposal.txt', b'Original proposal')})
            item = Contribution.objects.get(title=acronym + ' proposal')
            self.assertFalse(item.notifications_queued)
            ContributionRecipient.objects.create(group=group, email=address)
            ContributionRecipient.objects.create(group=group, email='inactive@example.test', active=False)
            queue_notifications(item.pk)
            queue_notifications(item.pk)
            notification = OutgoingMail.objects.get(contribution=item)
            self.assertEqual(notification.recipient, address)
            self.assertTrue(deliver(notification.pk))
            self.assertFalse(deliver(notification.pk))
            self.assertEqual(mail.outbox[-1].to, [address])
            self.assertEqual(mail.outbox[-1].attachments[0].filename, 'proposal.txt')
            self.assertEqual(mail.outbox[-1].attachments[0].content, 'Original proposal')

    @override_settings(FGTIDA_MAIL_ENABLED=True, FGTIDA_MAIL_IMMEDIATE=False,
                       EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_revoked_recipient_cannot_receive_queued_contribution(self):
        from .models import Contribution, ContributionRecipient, OutgoingMail
        from .contributions import queue_notifications
        from .mail import deliver
        group = Group.objects.get(acronym='tidawg1')
        recipient = ContributionRecipient.objects.create(group=group, email='former-member@example.test')
        item = Contribution.objects.create(submitter=self.user, group=group, title='Private proposal',
            filename='private.txt',content=b'Private proposal',notifications_queued=False)
        queue_notifications(item.pk)
        message = OutgoingMail.objects.get(contribution=item)
        recipient.active = False
        recipient.save()
        self.assertFalse(deliver(message.pk))
        message.refresh_from_db()
        self.assertIsNone(message.sent)
        self.assertIsNotNone(message.expires)
        self.assertEqual(message.attempts, 0)

    def test_editor_cannot_modify_identity_or_unscoped_native_admin(self):
        from django.contrib.auth.models import Group as AuthGroup
        editor = get_user_model().objects.create_user('fg-editor', password='strong-test-password', is_staff=True)
        editor.groups.add(AuthGroup.objects.get(name='FG-TIDA Administrators'))
        self.client.force_login(editor)
        self.assertTrue(editor.has_perm('doc.add_document'))
        self.assertFalse(editor.has_perm('person.change_email'))
        self.assertFalse(editor.has_perm('auth.change_user'))
        self.assertFalse(editor.has_perm('meeting.change_schedulingevent'))
        self.assertEqual(self.client.get('/admin/doc/document/').status_code, 200)
        self.assertEqual(self.client.get('/admin/meeting/schedulingevent/').status_code, 404)
        self.assertEqual(self.client.get('/admin/auth/user/').status_code, 403)

    def test_document_type_is_canonical_and_not_category_stack(self):
        from ietf.name.models import DocTypeName
        doc = self.create_doc()
        DocTypeName.objects.filter(slug='tida').update(name='templates')
        doc.tags.add('tida-templates')
        page = self.client.get('/doc/')
        self.assertContains(page, '<td role="cell" data-label="Type">FG-TIDA document</td>', html=True)
        self.assertNotContains(page, '<td>templates<div>', html=False)

    def test_email_identity_remains_case_insensitive(self):
        email = Email.objects.create(address='Mixed.Case@example.test', person=core.actor_for(self.user), origin='test fixture')
        self.assertEqual(Email.objects.get(address='mixed.case@EXAMPLE.test').pk, email.pk)
        self.assertTrue(Email.objects.filter(address__icontains='mixed.case').exists())
        self.assertTrue(email.history.filter(address='mixed.case@EXAMPLE.test').exists())
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Email.objects.create(address='mixed.case@EXAMPLE.test', person=email.person, origin='test fixture')

    def test_email_identity_columns_and_foreign_keys_use_citext(self):
        from django.db import connection
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL identity storage check.')
        with connection.cursor() as cursor:
            cursor.execute("SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='person_email'::regclass AND attname='address'")
            self.assertEqual(cursor.fetchone()[0], 'citext')
            cursor.execute("SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='person_historicalemail'::regclass AND attname='address'")
            self.assertEqual(cursor.fetchone()[0], 'citext')
            cursor.execute("SELECT format_type(a.atttypid, a.atttypmod) FROM pg_constraint c CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum WHERE c.contype='f' AND c.confrelid='person_email'::regclass")
            types = [row[0] for row in cursor.fetchall()]
            self.assertTrue(types)
            self.assertEqual(set(types), {'citext'})

    def test_admin_upload_creates_native_document_and_history(self):
        doc = self.create_doc()
        self.assertEqual(doc.rev, '00')
        self.assertTrue(doc.history_set.exists())
        self.assertTrue(doc.docevent_set.filter(type='new_revision').exists())
        self.assertTrue(Path(doc.get_file_name()).exists())
        self.client.logout()
        self.assertContains(self.client.get('/doc/?name=Identity'), 'Native identity document')
        self.assertContains(self.client.get('/doc/fg-tida-native-test/'), 'Native Datatracker document content')
        response = self.client.get('/doc/fg-tida-native-test/download/00/')
        self.assertEqual(b''.join(response.streaming_content), b'Native Datatracker document content')

    def test_admin_revision_and_state_change_preserve_history(self):
        doc = self.create_doc()
        values = self.admin_payload(f'/admin/doc/document/{doc.pk}/change/', {
            'name': doc.name, 'title': doc.title, 'type': 'tida', 'group': self.root.pk,
            'abstract': doc.abstract, 'lifecycle_state': State.objects.get(type_id='tida', slug='approved').pk,
            'comment_about_changes': 'Approved second revision',
            'upload': SimpleUploadedFile('identity.txt', b'Second native revision'), '_save': 'Save',
        })
        response = self.client.post(f'/admin/doc/document/{doc.pk}/change/', values)
        self.assertEqual(response.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.rev, '01')
        self.assertEqual(doc.get_state_slug('tida'), 'approved')
        old = self.client.get('/doc/fg-tida-native-test/download/00/')
        self.assertEqual(b''.join(old.streaming_content), b'Native Datatracker document content')

    def test_admin_meeting_uses_native_schedule_and_public_lists(self):
        day = timezone.localdate() + timedelta(days=10)
        response = self.client.post('/admin/meeting/meetingcategory/add/', {'name': 'Interoperability session', '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        category = MeetingCategory.objects.get(name='Interoperability session')
        data = self.admin_payload('/admin/meeting/meeting/add/', {'number': 'tida-future-test',
            'category': category.pk, 'date': day, 'days': 2, 'city': 'Paris', 'country': 'FR', 'time_zone': 'Europe/Paris', '_save': 'Save'})
        response = self.client.post('/admin/meeting/meeting/add/', data)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['adminform'].form.errors)
        meeting = Meeting.objects.get(number='tida-future-test')
        self.assertEqual(meeting.type_id, 'tida')
        self.assertEqual(meeting.category, category)
        self.assertIsNotNone(meeting.schedule_id)
        self.client.logout()
        self.assertContains(self.client.get('/meeting/upcoming/'), 'tida-future-test')
        self.assertNotContains(self.client.get('/meeting/past/'), 'tida-future-test')
        self.assertContains(self.client.get('/meeting/tida-future-test/agenda/'), 'Interoperability session')

    def test_admin_is_required_and_catalog_account_header_is_gone(self):
        self.client.logout()
        self.assertEqual(self.client.get('/admin/doc/document/add/').status_code, 302)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '>Account</a>')
        self.assertNotContains(response, 'Refresh sources')
        self.assertContains(response, 'Sign in')

    def test_scope_uses_group_relationships_not_github(self):
        child = Group.objects.create(acronym='identity', name='Identity', type_id='wg', state_id='active', parent=self.root)
        doc = Document.objects.create(name='native-child', title='Child group document', type_id='tida', group=child)
        self.assertIn(doc, core.documents())
        self.assertContains(self.client.get('/group/identity/about/'), 'Child group document')

    def test_repository_support_cleanup_is_reversible_and_idempotent(self):
        doc = Document.objects.create(name='fg-tida-spec-template-changelog-286', title='CHANGELOG', type_id='tida', group=self.root)
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / 'manifest.json'
            manifest.write_text(json.dumps([{'model': 'tida.resource', 'pk': 286, 'fields': {
                'repository': 'spec-template', 'source_path': 'CHANGELOG.md', 'title': 'CHANGELOG'}}]))
            for _ in range(2):
                call_command('curate_fgtida_catalog', archive=str(manifest), apply=True, skip_checks=True, stdout=StringIO())
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
        self.assertEqual(core.documents().filter(type_id='tida-template').count(), 2)
        self.assertEqual(self.client.get(f'/doc/{doc.name}/').status_code, 404)
        self.assertNotContains(self.client.get('/doc/'), 'CHANGELOG')
        self.assertEqual(doc.docevent_set.count(), 1)

    def test_members_photos_profiles_and_role_validation(self):
        person = Person.objects.create(name='Member Example', ascii='Member Example')
        photo_bytes = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9HkAAAAASUVORK5CYII=')
        person.photo.save('member.png', ContentFile(photo_bytes))
        email = Email.objects.create(address='member@example.test', person=person, origin='test fixture')
        response = self.client.post('/admin/group/role/add/', {'group': self.root.pk, 'name': 'member', 'person': person.pk, 'email': email.pk, '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.root.groupevent_set.exists())
        self.client.logout()
        for section in ('members', 'photos'):
            self.assertContains(self.client.get(f'/group/tida/{section}/'), 'Member Example')
        self.assertContains(self.client.get(f'/people/{person.pk}/'), 'Member Example')
        photo = self.client.get(f'/people/{person.pk}/photo/')
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(b''.join(photo.streaming_content), photo_bytes)
        outsider = Person.objects.create(name='Unlisted Person', ascii='Unlisted Person')
        self.assertEqual(self.client.get(f'/people/{outsider.pk}/').status_code, 404)

    def test_formats_text_and_revision_comparison(self):
        doc = self.create_doc()
        core.save_document(doc, core.actor_for(self.user), 'Changed text', data=b'Updated content', filename='revision.md')
        response = self.client.get(f'/doc/{doc.name}/')
        self.assertContains(response, 'bi-file-text')
        self.assertNotContains(response, 'bi-file-pdf')
        text = self.client.get(f'/doc/{doc.name}/text/01/')
        self.assertEqual(b''.join(text.streaming_content), b'Updated content')
        diff = self.client.get(f'/doc/{doc.name}/compare/?from=00&to=01')
        self.assertContains(diff, '-Native Datatracker document content')
        self.assertContains(diff, '+Updated content')

    def test_recordings_materials_and_calendar_use_native_relationships(self):
        meeting = Meeting.objects.create(number='tida-calendar-test', type_id='tida', date=timezone.localdate(), days=2, time_zone='Europe/Paris')
        schedule = Schedule.objects.create(meeting=meeting, name='official', owner=core.actor_for(self.user))
        meeting.schedule = schedule
        meeting.save()
        session = Session.objects.create(meeting=meeting, group=self.root, name='Identity session', type_id='session', purpose_id='regular')
        slot = TimeSlot.objects.create(meeting=meeting, type_id='session', name='Morning', time=timezone.now(), duration=timedelta(hours=1))
        SchedTimeSessAssignment.objects.create(schedule=schedule, session=session, timeslot=slot)
        recording = Document.objects.create(name='tida-recording-test', title='Session recording', type_id='recording', group=self.root, external_url='https://example.test/recording')
        SessionPresentation.objects.create(session=session, document=recording)
        hidden = Document.objects.create(name='tida-hidden-material', title='Hidden support file', type_id='tida', group=self.root)
        hidden.tags.add('tida-uncatalogued')
        SessionPresentation.objects.create(session=session, document=hidden)
        response = self.client.get(f'/meeting/{meeting.number}/materials/')
        self.assertContains(response, 'bi-camera-video')
        self.assertNotContains(response, 'Hidden support file')
        self.assertContains(self.client.get(f'/meeting/{meeting.number}/agenda/?tz=UTC'), 'Showing times in <strong>UTC</strong>')
        from icalendar import Calendar
        calendar = Calendar.from_ical(self.client.get(f'/meeting/{meeting.number}/calendar.ics').content)
        event = calendar.walk('VEVENT')[0]
        self.assertEqual(str(event['summary']), 'Identity session')
        self.assertEqual(event.decoded('dtend') - event.decoded('dtstart'), timedelta(hours=1))
        self.assertContains(self.client.get('/group/tida/meetings/'), 'tida-calendar-test')

    def test_catalog_visibility_is_admin_managed(self):
        doc = self.create_doc()
        values = self.admin_payload(f'/admin/doc/document/{doc.pk}/change/', {'name': doc.name, 'title': doc.title,
            'type': 'tida', 'group': self.root.pk, 'abstract': doc.abstract, 'catalogued': '',
            'lifecycle_state': State.objects.get(type_id='tida', slug='draft').pk,
            'comment_about_changes': 'Exclude support record', '_save': 'Save'})
        self.assertEqual(self.client.post(f'/admin/doc/document/{doc.pk}/change/', values).status_code, 302)
        self.assertEqual(self.client.get(f'/doc/{doc.name}/').status_code, 404)
        self.assertEqual(self.client.get(f'/doc/{doc.name}/download/00/').status_code, 404)
        self.assertTrue(Path(doc.get_file_name()).is_file())

    def test_admin_deadlines_and_milestone_choices_are_fg_tida_scoped(self):
        meeting = Meeting.objects.create(number='tida-deadlines', type_id='tida', date=timezone.localdate(), days=1)
        date = timezone.localdate() + timedelta(days=3)
        response = self.client.post('/admin/name/importantdatename/add/', {'slug': 'agenda-publication',
            'name': 'Agenda publication', 'used': 'on', 'order': 0, 'default_offset_days': 0, '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        response = self.client.post('/admin/meeting/importantdate/add/', {'meeting': meeting.pk,
            'name': 'tida-agenda-publication', 'date': date.isoformat(), '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ImportantDate.objects.filter(meeting=meeting, name_id='tida-agenda-publication').exists())
        self.assertContains(self.client.get('/meeting/tida-deadlines/important-dates/'), 'Agenda publication')
        response = self.client.get('/admin/group/groupmilestone/add/')
        self.assertEqual(response.status_code, 200)
        choices = response.context['adminform'].form.fields['group'].queryset
        self.assertEqual(set(choices.values_list('pk', flat=True)), set(core.groups().values_list('pk', flat=True)))

    def test_documents_and_future_meetings_paginate_as_records_grow(self):
        Document.objects.bulk_create([Document(name=f'scale-doc-{index}', title=f'Scale document {index}',
            type_id='tida', group=self.root) for index in range(26)])
        future = timezone.localdate() + timedelta(days=300)
        Meeting.objects.bulk_create([Meeting(number=f'tida-scale-{index}', type_id='tida', date=future + timedelta(days=index), days=1)
            for index in range(26)])
        for url in ('/doc/?q=Scale', '/meeting/upcoming/?q=tida-scale'):
            first = self.client.get(url)
            self.assertEqual(len(first.context['page']), 25)
            second = self.client.get(url + '&page=2')
            self.assertEqual(len(second.context['page']), 1)

    def test_central_important_dates_and_registration_menu(self):
        meeting = Meeting.objects.create(number='official-date-test', type_id='tida', date=timezone.localdate(), days=1)
        Meeting.objects.create(number='no-published-dates', type_id='tida', date=timezone.localdate(), days=1)
        ImportantDate.objects.create(meeting=meeting, name_id='tida-agenda', date=timezone.localdate())
        response = self.client.get('/meeting/important-dates/')
        self.assertContains(response, 'official-date-test')
        self.assertContains(response, 'Agenda publication')
        self.assertNotContains(response, 'no-published-dates')
        self.assertContains(response, 'Weekday')
        self.assertContains(response, 'tsbfgtida@itu.int')
        self.assertNotContains(response, 'href="/meeting/calendar.ics"')
        self.assertRedirects(self.client.get('/meeting/registration/'),
            'https://www.itu.int/en/ITU-T/focusgroups/tida/Pages/default.aspx', fetch_redirect_response=False)

    def test_agenda_empty_state_and_more_than_one_page_of_sessions(self):
        meeting = Meeting.objects.create(number='agenda-scale', type_id='tida', date=timezone.localdate(), days=1, time_zone='Europe/Paris')
        self.assertContains(self.client.get('/meeting/agenda-scale/agenda/'), 'Agenda not published yet')
        schedule = Schedule.objects.create(meeting=meeting, name='official', owner=core.actor_for(self.user))
        meeting.schedule = schedule
        meeting.save()
        slot = TimeSlot.objects.create(meeting=meeting, type_id='session', name='Morning', time=timezone.now(), duration=timedelta(hours=1))
        for index in range(26):
            session = Session.objects.create(meeting=meeting, group=self.root, name=f'Scheduled item {index}', type_id='session', purpose_id='regular')
            SchedTimeSessAssignment.objects.create(schedule=schedule, session=session, timeslot=slot)
        response = self.client.get('/meeting/agenda-scale/agenda?tz=UTC')
        self.assertContains(response, 'Scheduled item 25')
        self.assertContains(response, 'Showing times in <strong>UTC</strong>')
        self.assertContains(response, 'Agenda days')
        filtered = self.client.get('/meeting/agenda-scale/agenda/?q=Scheduled+item+25')
        self.assertContains(filtered, 'Scheduled item 25')
        self.assertNotContains(filtered, 'Scheduled item 24')

    def test_user_menu_preferences_and_markdown(self):
        response = self.client.get('/doc/')
        self.assertContains(response, 'fg-tida-logo.png')
        self.assertContains(response, 'Manage users')
        self.client.logout()
        response = self.client.get('/doc/')
        self.assertContains(response, 'Password reset')
        self.assertContains(response, 'New account')
        self.assertNotContains(response, 'Manage users')
        for url in ('/accounts/reset/', '/accounts/create/', '/accounts/settings/'):
            self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.get('/accounts/settings/')
        self.assertContains(response, 'Agendas open in Meeting time')
        from .templatetags.fgtida import rich_text
        rendered = rich_text('## Heading\n\n- First\n- Second\n\n<script>alert(1)</script>\n\n[Bad](javascript:alert(1))')
        self.assertIn('<h2', rendered)
        self.assertIn('<li>First</li>', rendered)
        self.assertNotIn('## Heading', rendered)
        self.assertNotIn('<script', rendered)
        self.assertNotIn('href="javascript:', rendered)
        table = rich_text('**Mandates in this case** *(required)*\n\u00a0\n| Grantor | Grantee | What is conferred | Governing regime |\n|---|---|---|---|\n| E | A | Authority | Policy |')
        self.assertIn('<table>', table)
        self.assertIn('<th>Grantor</th>', table)
        self.assertIn('<td>Authority</td>', table)

    def test_agenda_local_timezone_regroups_day_and_rejects_invalid_zone(self):
        from datetime import datetime, timezone as datetime_timezone
        meeting = Meeting.objects.create(number='local-zone', type_id='tida', date=timezone.localdate(), days=1, time_zone='Europe/Paris')
        schedule = Schedule.objects.create(meeting=meeting, name='official', owner=core.actor_for(self.user))
        meeting.schedule = schedule
        meeting.save()
        session = Session.objects.create(meeting=meeting, group=self.root, name='Local day test', type_id='session', purpose_id='regular')
        self.client.cookies['fgtida_agenda_zone'] = 'local'
        response = self.client.get('/meeting/local-zone/agenda/')
        self.assertEqual(response.context['zone_mode'], 'meeting')
        self.assertEqual(response.context['display_zone'], 'Europe/Paris')

        slot = TimeSlot.objects.create(meeting=meeting, type_id='session', name='Early', time=datetime(2026, 10, 24, 0, 30, tzinfo=datetime_timezone.utc), duration=timedelta(hours=1))
        SchedTimeSessAssignment.objects.create(schedule=schedule, session=session, timeslot=slot)
        response = self.client.get('/meeting/local-zone/agenda/', {'tz': 'local', 'local_zone': 'America/Los_Angeles'})
        self.assertEqual(response.context['display_zone'], 'America/Los_Angeles')
        self.assertEqual(response.context['days'][0][0].isoformat(), '2026-10-23')
        self.assertContains(response, '17:30')
        response = self.client.get('/meeting/local-zone/agenda/', {'tz': 'local', 'local_zone': '../../invalid'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['display_zone'], 'Europe/Paris')

    def test_session_status_admin_and_cancellation_archive(self):
        from . import workflow
        from icalendar import Calendar
        meeting = Meeting.objects.create(number='status-workflow', type_id='tida', date=timezone.localdate(), days=1, time_zone='UTC')
        values = self.admin_payload('/admin/meeting/session/add/', {'meeting': meeting.pk,
            'name': 'Workflow session', 'group': self.root.pk, 'type': 'session', 'purpose': 'regular',
            'scheduling_status': 'apprw', 'on_agenda': 'on', '_save': 'Save'})
        response = self.client.post('/admin/meeting/session/add/', values)
        self.assertEqual(response.status_code, 302)
        session = Session.objects.get(meeting=meeting, name='Workflow session')
        self.assertEqual(session.schedulingevent_set.get().status_id, 'apprw')
        schedule = Schedule.objects.create(meeting=meeting, name='official', owner=core.actor_for(self.user))
        meeting.schedule = schedule
        meeting.save()
        slot = TimeSlot.objects.create(meeting=meeting, type_id='session', name='Slot', time=timezone.now(), duration=timedelta(hours=1))
        SchedTimeSessAssignment.objects.create(schedule=schedule, session=session, timeslot=slot)
        session.remote_instructions = 'Join https://example.test/live-session'
        session.save()
        actor = core.actor_for(self.user)
        workflow.set_session_status(session, 'sched', actor)
        workflow.set_session_status(session, 'sched', actor)
        self.assertEqual(session.schedulingevent_set.count(), 2)
        self.assertContains(self.client.get('/meeting/upcoming/?status=sched'), 'Workflow session')
        before = Calendar.from_ical(self.client.get('/meeting/status-workflow/calendar.ics').content).walk('VEVENT')[0]
        self.assertEqual(str(before['status']), 'CONFIRMED')
        response = self.client.post('/admin/meeting/meeting/', {'action': 'cancel_sessions', '_selected_action': [meeting.pk]})
        self.assertEqual(response.status_code, 302)
        page = self.client.get('/meeting/status-workflow/agenda/')
        self.assertContains(page, 'This session is cancelled')
        self.assertContains(page, 'Scheduling history')
        self.assertNotContains(page, 'https://example.test/live-session')
        self.assertContains(self.client.get('/meeting/upcoming/?status=canceled'), 'Workflow session')
        self.assertNotContains(self.client.get('/meeting/upcoming/?status=sched'), 'Workflow session')
        after = Calendar.from_ical(self.client.get('/meeting/status-workflow/calendar.ics').content).walk('VEVENT')[0]
        self.assertEqual(after['uid'], before['uid'])
        self.assertEqual(str(after['status']), 'CANCELLED')
        self.assertEqual(session.schedulingevent_set.count(), 3)

    def test_document_recency_categories_and_lifecycle_markers(self):
        from .workflow import recent_marker
        doc = self.create_doc()
        doc.tags.add('tida-deliverables')
        self.assertEqual(recent_marker(doc), 'New')
        self.assertContains(self.client.get('/doc/?recent=new'), doc.title)
        doc.docevent_set.update(time=timezone.now() - timedelta(days=30))
        self.assertNotContains(self.client.get('/doc/?recent=new'), doc.title)
        core.save_document(doc, core.actor_for(self.user), 'Revision for review', data=b'Revised material', filename='revision.txt',
            state=State.objects.get(type_id='tida', slug='review'))
        page = self.client.get('/doc/?recent=updated&category=deliverables&state=review&sort=title')
        self.assertContains(page, doc.title)
        self.assertContains(page, '>Updated</span>')
        self.assertContains(page, 'text-bg-warning')
        self.assertContains(page, '>Deliverables</a>')
        self.assertEqual(recent_marker(doc), 'Updated')
        self.assertNotContains(self.client.get('/doc/?state=published'), doc.title)
