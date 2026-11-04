import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import runpy
import secrets
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

from django.core.exceptions import DisallowedHost, ImproperlyConfigured
from django.core.management import call_command
from django.db import OperationalError
from django.http import HttpResponse
from django.test import SimpleTestCase, RequestFactory, override_settings

from .render_settings import apply_render_settings
from .render_http import RenderEdgeMiddleware
from .browser_security import BrowserSecurityMiddleware


class BrowserSecurityTests(SimpleTestCase):
    def test_html_blocks_inline_scripts_embedding_and_off_origin_forms(self):
        response = BrowserSecurityMiddleware(lambda request: HttpResponse('<html></html>'))(
            RequestFactory().get('/'))
        policy = response['Content-Security-Policy']
        self.assertIn("script-src 'self';", policy)
        self.assertIn("script-src-attr 'none'", policy)
        self.assertIn("form-action 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn('unsafe-eval', policy)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", policy)
        self.assertEqual(response['Permissions-Policy'], 'camera=(), microphone=(), geolocation=()')

    def test_pdf_and_recording_responses_are_not_given_html_policy(self):
        for mime in ('application/pdf', 'video/mp4', 'audio/mpeg', 'text/plain'):
            response = BrowserSecurityMiddleware(lambda request: HttpResponse(b'file', content_type=mime))(
                RequestFactory().get('/file/'))
            self.assertNotIn('Content-Security-Policy', response)


@override_settings(ALLOWED_HOSTS=['tracker.example.test', 'tracker.onrender.com'],
                   FGTIDA_PUBLIC_DOMAIN='tracker.example.test', SITE_URL='https://tracker.example.test')
class RenderProfileTests(SimpleTestCase):
    def test_empty_disk_directories_are_created_without_replacing_uploads(self):
        prepare = runpy.run_path('deploy/render_start.py')['prepare_data_directories']
        with tempfile.TemporaryDirectory() as folder:
            prepare(folder)
            photo = Path(folder) / 'media/photo/existing.jpg'
            photo.write_bytes(b'preserve existing upload')
            prepare(folder)
            self.assertEqual(photo.read_bytes(), b'preserve existing upload')
            for relative in ('media/photo', 'proceedings', 'documents', 'charters',
                             'internet-drafts', 'rfc', 'static'):
                self.assertTrue((Path(folder) / relative).is_dir())

    def test_disk_directory_symlink_cannot_escape_mount(self):
        prepare = runpy.run_path('deploy/render_start.py')['prepare_data_directories']
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            (Path(folder) / 'media').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesMessage(RuntimeError, 'outside the persistent disk'):
                prepare(folder)
            self.assertFalse((Path(outside) / 'photo').exists())

    def test_bootstrap_exposes_no_files_or_application(self):
        application = runpy.run_path('deploy/render_bootstrap.py')['application']
        for path, expected in [('/health/', '200 OK'), ('/', '503 Service Unavailable'),
                               ('/etc/secrets/db_password.key', '503 Service Unavailable'),
                               ('/admin/', '503 Service Unavailable'), ('/static/x', '503 Service Unavailable')]:
            headers = []
            body = b''.join(application({'PATH_INFO': path, 'REQUEST_METHOD': 'GET'},
                                       lambda status, values: headers.append((status, values))))
            self.assertEqual(headers[0][0], expected)
            self.assertIn(b'Maintenance', body)

    def test_health_checks_database_without_https_or_session(self):
        middleware = RenderEdgeMiddleware(lambda request: self.fail('Health reached application.'))
        with patch('ietf.fgtida.render_http.connection') as database:
            response = middleware(RequestFactory().get('/health/', HTTP_HOST='tracker.onrender.com'))
            self.assertEqual(response.status_code, 200)
            database.cursor.return_value.__enter__.return_value.execute.assert_called_once_with('SELECT 1')
        with patch('ietf.fgtida.render_http.connection') as database:
            database.cursor.side_effect = OperationalError('private database error')
            response = middleware(RequestFactory().get('/health/', HTTP_HOST='tracker.onrender.com'))
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b'private', response.content)
        with self.assertRaises(DisallowedHost):
            middleware(RequestFactory().get('/health/', HTTP_HOST='untrusted.example'))

    def test_canonical_host_and_posts(self):
        middleware = RenderEdgeMiddleware(lambda request: HttpResponse('application'))
        response = middleware(RequestFactory().get('/meeting/?tz=utc', HTTP_HOST='tracker.onrender.com'))
        self.assertEqual(response['Location'], 'https://tracker.example.test/meeting/?tz=utc')
        self.assertEqual(middleware(RequestFactory().post('/accounts/login/', HTTP_HOST='tracker.onrender.com')).status_code, 400)
        self.assertEqual(middleware(RequestFactory().get('/', HTTP_HOST='tracker.example.test')).content, b'application')

    def test_no_broker_required_and_only_loopback_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            secret = Path(folder) / 'test.key'
            secret.write_text(secrets.token_urlsafe(64))
            env = {'FGTIDA_ENV': 'production', 'FGTIDA_HOSTING': 'render',
                   'FGTIDA_SECRET_KEY_FILE': str(secret), 'FGTIDA_DB_PASSWORD_FILE': str(secret),
                   'FGTIDA_ORIGIN': 'https://tracker.example.test',
                   'FGTIDA_APPROVED_PRIVILEGED_USERS': 'admin-example',
                   'FGTIDA_DB_HOST': 'db', 'FGTIDA_DB_NAME': 'fgtida', 'FGTIDA_DB_USER': 'app',
                   'FGTIDA_DATA_DIR': folder, 'RENDER_EXTERNAL_HOSTNAME': 'tracker.onrender.com'}
            with patch.dict(os.environ, env, clear=True):
                config = runpy.run_path(str(Path(__file__).with_name('production_settings.py')))
                config['MIDDLEWARE'] = ['django.middleware.security.SecurityMiddleware', 'example.Middleware']
                apply_render_settings(config)
                self.assertIsNone(config['CELERY_BROKER_URL'])
                self.assertFalse(config['DEBUG'])
                self.assertIn('tracker.onrender.com', config['ALLOWED_HOSTS'])
                self.assertEqual(config['CSRF_TRUSTED_ORIGINS'], ['https://tracker.example.test'])
                self.assertEqual(config['CACHES']['default']['LOCATION'], '127.0.0.1:11211')
                self.assertFalse(config['SECURE_HSTS_INCLUDE_SUBDOMAINS'])
                self.assertFalse(config['SECURE_HSTS_PRELOAD'])
                self.assertIn('security.W021', config['SILENCED_SYSTEM_CHECKS'])
                self.assertNotIn('security.W005', config['SILENCED_SYSTEM_CHECKS'])
                os.environ['FGTIDA_HSTS_INCLUDE_SUBDOMAINS'] = 'true'
                opted_in = runpy.run_path(str(Path(__file__).with_name('production_settings.py')))
                self.assertTrue(opted_in['SECURE_HSTS_INCLUDE_SUBDOMAINS'])
                os.environ['RENDER_EXTERNAL_HOSTNAME'] = '*.onrender.com'
                with self.assertRaises(ImproperlyConfigured):
                    apply_render_settings(config)

    @override_settings(SECURE_HSTS_SECONDS=31536000, SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
                       SECURE_HSTS_PRELOAD=False)
    def test_https_policy_covers_subdomains_without_browser_preload(self):
        from django.middleware.security import SecurityMiddleware
        response = SecurityMiddleware(lambda request: HttpResponse('OK'))(
            RequestFactory().get('/', secure=True, HTTP_HOST='tracker.example.test'))
        self.assertEqual(response['Strict-Transport-Security'], 'max-age=31536000; includeSubDomains')

    def test_celery_cannot_silently_publish(self):
        from ietf.celeryapp import app, debug_task
        with patch.dict(os.environ, {'FGTIDA_HOSTING': 'render'}):
            with self.assertRaisesMessage(RuntimeError, 'Celery publishing is disabled'):
                app.send_task('example.unconfigured')
            with self.assertRaisesMessage(RuntimeError, 'Celery publishing is disabled'):
                debug_task.delay()

    def test_removed_api_is_absent_from_routes_and_generated_schema(self):
        from django.urls import resolve, Resolver404
        from drf_spectacular.generators import SchemaGenerator
        for path in ('/api/', '/api/schema/', '/api/red/doc/', '/accounts/apikey/',
                     '/accounts/apikey/add/', '/accounts/apikey/del/'):
            with self.assertRaises(Resolver404):
                resolve(path)
        schema = SchemaGenerator().get_schema(request=None, public=True)
        self.assertFalse(any(path.startswith('/api/') for path in schema['paths']))


class ArtifactRestoreTests(SimpleTestCase):
    def archive(self, folder, name, files, corrupt=False):
        target = Path(folder) / name
        manifest = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
        if corrupt:
            manifest[next(iter(manifest))] = '0' * 64
        with ZipFile(target, 'w') as archive:
            for path, data in files.items():
                archive.writestr(path, data)
            archive.writestr('manifest.sha256.json', json.dumps(manifest))
        return target

    def test_restore_is_idempotent_and_checks_all_files_first(self):
        from django.core.management.base import CommandError
        with tempfile.TemporaryDirectory() as folder, override_settings(FGTIDA_DATA_DIR=folder):
            archive = self.archive(folder, 'good.zip', {'documents/tida/a.txt': b'approved'})
            for attempt in range(2):
                call_command('restore_fgtida_files', archive=str(archive), stdout=StringIO())
            self.assertEqual((Path(folder) / 'documents/tida/a.txt').read_bytes(), b'approved')
            archive = self.archive(folder, 'bad.zip', {'documents/tida/b.txt': b'bad'}, corrupt=True)
            with self.assertRaises(CommandError):
                call_command('restore_fgtida_files', archive=str(archive), stdout=StringIO())
            self.assertFalse((Path(folder) / 'documents/tida/b.txt').exists())
            archive = self.archive(folder, 'overwrite.zip', {'documents/tida/a.txt': b'other'})
            with self.assertRaises(CommandError):
                call_command('restore_fgtida_files', archive=str(archive), stdout=StringIO())

    def test_traversal_and_unapproved_files_are_rejected(self):
        from django.core.management.base import CommandError
        with tempfile.TemporaryDirectory() as folder, override_settings(FGTIDA_DATA_DIR=folder):
            for index, name in enumerate(['../escape', 'media/../../escape', '/tmp/escape',
                                           'static/secret.key', 'media\\..\\escape']):
                archive = self.archive(folder, f'bad{index}.zip', {name: b'bad'})
                with self.assertRaises(CommandError):
                    call_command('restore_fgtida_files', archive=str(archive), stdout=StringIO())
