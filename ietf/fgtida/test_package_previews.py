import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from PIL import Image
from ietf.group.models import Group

from . import core, tests as native_tests
from .models import Contribution
from .package_previews import chapters
from .source_packages import pack
from .templatetags.fgtida import rich_rst


def image_bytes(color):
    stream = io.BytesIO()
    Image.new('RGB', (2, 2), color).save(stream, format='PNG')
    return stream.getvalue()


def source(color='red'):
    return pack({
        'index.rst': b'Illustrated proposal\n====================\n\nIntroduction.\n',
        'chapters/detail.rst': b'Detail\n======\n\n.. figure:: ../figures/a.png\n   :alt: Architecture diagram\n\n   Diagram caption.\n\n.. |icon| image:: ../figures/vector.svg\n\nAn |icon| in the text.\n',
        'figures/a.png': image_bytes(color),
        'figures/vector.svg': b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2"/></svg>',
    })


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PackageRenderingTests(SimpleTestCase):
    def test_word_conversion_images_use_the_same_preview_routes(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from .source_packages import convert_word
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'diagram.png').write_bytes(image_bytes('red'))
            word = subprocess.run(['pandoc', '-f', 'rst', '-t', 'docx'], cwd=folder,
                input=b'Illustrated\n===========\n\n.. image:: diagram.png\n',
                check=True, capture_output=True).stdout
        archive, _ = convert_word(word)
        _, text, context = chapters(archive, lambda path: '/converted/' + path)[0]
        rendered = str(rich_rst(text, context))
        self.assertTrue(context['image_urls'])
        for url in context['image_urls']:
            self.assertIn(f'src="{url}"', rendered)

    def test_relative_figures_substitutions_and_captions(self):
        rows = chapters(source(), lambda path: '/safe-images/00/' + path)
        name, text, context = rows[0]
        self.assertEqual(name, 'chapters/detail.rst')
        rendered = str(rich_rst(text, context))
        self.assertIn('src="/safe-images/00/figures/a.png"', rendered)
        self.assertIn('src="/safe-images/00/figures/vector.svg"', rendered)
        self.assertIn('alt="Architecture diagram"', rendered)
        self.assertIn('Diagram caption.', rendered)

    def test_external_missing_traversal_and_active_images_never_get_sources(self):
        payload = pack({'index.rst': b'.. image:: https://evil.example/tracker.png\n\n.. image:: /etc/passwd\n\n.. image:: ../outside.png\n\n.. image:: missing.png\n\n.. image:: diagram.puml\n',
                        'diagram.puml': b'@startuml'})
        _, text, context = chapters(payload, lambda path: '/safe/' + path)[0]
        self.assertEqual(context['image_urls'], set())
        self.assertNotIn('<img', str(rich_rst(text, context)))
        from .rendering import clean_fragment
        result = clean_fragment('<img src="https://evil.example/pixel" onerror="alert(1)"><img src="/safe/a.png" onload="bad()">',
                                image_urls={'/safe/a.png'})
        self.assertNotIn('https://evil.example', result)
        self.assertNotIn('onerror', result)
        self.assertNotIn('onload', result)
        self.assertIn('src="/safe/a.png"', result)
        # A cached render in another context must not inherit image permissions.
        self.assertNotIn('<img', str(rich_rst('.. image:: /safe/a.png')))


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_MAIL_ENABLED=False,
                   FGTIDA_MAIL_IMMEDIATE=False, FGTIDA_UPLOAD_SCAN_REQUIRED=False)
class PackageAccessTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def submission(self):
        data = source()
        response = self.client.post('/submit/', {'title': 'Illustrated proposal', 'group': Group.objects.get(acronym='tidawg1').pk,
            'submission_method': 'rst', 'publication_consent': 'on', 'entry_document': 'index.rst',
            'upload': SimpleUploadedFile('proposal.zip', data)})
        self.assertEqual(response.status_code, 302)
        return Contribution.objects.get()

    def test_public_and_private_previews_and_asset_permissions(self):
        item = self.submission()
        path = 'figures/a.png'
        private = reverse('fgtida:contribution-asset', args=[item.pk, path])
        public = reverse('fgtida:document-image', args=[item.document.name, item.document.rev, path])
        self.assertContains(self.client.get(f'/submit/{item.pk}/'), f'src="{private}"')
        self.assertEqual(self.client.get(private).content, image_bytes('red'))
        self.client.logout()
        self.assertContains(self.client.get(f'/doc/{item.document.name}/'), f'src="{public}"')
        response = self.client.get(public)
        self.assertEqual(response.content, image_bytes('red'))
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(self.client.get(private).status_code, 302)
        other = get_user_model().objects.create_user('preview-reader', password='test-password')
        self.client.force_login(other)
        self.assertEqual(self.client.get(private).status_code, 404)
        self.assertEqual(self.client.get(public.replace('figures/a.png', 'index.rst')).status_code, 404)
        self.assertEqual(self.client.get(public.replace('figures/a.png', 'missing.png')).status_code, 404)

    def test_scanner_status_blocks_images_even_at_direct_urls(self):
        item = self.submission()
        public = reverse('fgtida:document-image', args=[item.document.name, item.document.rev, 'figures/a.png'])
        private = reverse('fgtida:contribution-asset', args=[item.pk, 'figures/a.png'])
        with override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=True):
            self.assertEqual(self.client.get(public).status_code, 403)
            self.assertEqual(self.client.get(private).status_code, 403)
            page = self.client.get(f'/doc/{item.document.name}/')
            self.assertNotContains(page, f'src="{public}"')
            import hashlib
            from .models import FileScan
            data = bytes(item.source_archive)
            scan = FileScan.objects.create(sha256=hashlib.sha256(data).hexdigest(),
                filename='source.zip', size=len(data), status='clean', formats=['zip'])
            self.assertEqual(self.client.get(public).status_code, 200)
            self.assertEqual(self.client.get(private).status_code, 200)
            # A previously working URL must stop serving after a later rejection.
            scan.status = 'rejected'
            scan.save()
            self.assertEqual(self.client.get(public).status_code, 403)
            self.assertEqual(self.client.get(private).status_code, 403)

    def test_archive_revisions_keep_their_own_images(self):
        item = self.submission()
        doc = item.document
        old_rev = doc.rev
        core.save_document(doc, core.actor_for(self.user), 'Updated illustrated version',
                           data=source('blue'), filename='proposal.zip')
        self.assertNotEqual(doc.rev, old_rev)
        for revision, color in [(old_rev, 'red'), (doc.rev, 'blue')]:
            url = reverse('fgtida:document-image', args=[doc.name, revision, 'figures/a.png'])
            self.assertEqual(self.client.get(url).content, image_bytes(color))
            self.assertContains(self.client.get(reverse('fgtida:revision', args=[doc.name, revision])), f'src="{url}"')
