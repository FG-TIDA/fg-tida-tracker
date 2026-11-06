import io
import zipfile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase, override_settings
from ietf.doc.models import Document
from . import tests as native_tests
from .models import DocumentFormat


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RichRenderingTests(SimpleTestCase):
    def test_github_tables_spans_links_and_soft_paragraphs(self):
        from .templatetags.fgtida import rich_text
        rendered = str(rich_text('Soft wrapped\nparagraph.\n\n| A | B |\n|---|---|\n| one | two |\n\n<table><tr><td colspan="2">Merged</td></tr></table>\n\n[Related](chapter.md)\n\n> [!NOTE]\n> A note.', 'https://github.com/example/repo/blob/main/source.md'))
        for expected in ['<table>', 'colspan="2"', 'https://github.com/example/repo/blob/main/chapter.md', '<strong>Note</strong>']:
            self.assertIn(expected, rendered)
        self.assertNotIn('wrapped<br', rendered)

    def test_short_dates_versions_and_table_headings_keep_readable_widths(self):
        from .templatetags.fgtida import rich_text, rich_rst
        rendered = str(rich_text('| Version | Date | Change |\n|---|---|---|\n| 0.31 | 2026-10-30 | A long change description |'))
        self.assertIn('<span class="fg-atomic">0.31</span>', rendered)
        self.assertIn('<span class="fg-atomic">2026-10-30</span>', rendered)
        self.assertIn('<th>Version</th>', rendered)
        self.assertIn('fg-atomic', str(rich_rst('.. list-table::\n   :header-rows: 1\n\n   * - Date\n     - Change\n   * - 2026-10-30\n     - Description')))

    def test_untrusted_html_and_code_examples_are_safe(self):
        from .templatetags.fgtida import rich_text
        rendered = str(rich_text('<script>alert(1)</script><style>body{display:none}</style><a href="javascript:alert(1)">Bad</a><td colspan="x" onclick="bad()">Cell</td>\n\n```html\n<script>example</script>\n```'))
        for forbidden in ['<script>', '<style>', 'href="javascript:', 'onclick=', 'colspan="x"']:
            self.assertNotIn(forbidden, rendered)
        self.assertIn('&lt;script&gt;example&lt;/script&gt;', rendered)

    def test_sphinx_html_sections_list_tables_and_references(self):
        from .templatetags.fgtida import rich_rst
        rendered = str(rich_rst('.. _target:\n\nHeading\n=======\n\n.. only:: html\n\n   Visible HTML content.\n\n.. only:: latex\n\n   Hidden print content.\n\n.. list-table::\n   :header-rows: 1\n\n   * - Name\n     - Value\n   * - One\n     - Two\n\nSee :ref:`Heading <target>`.\n\n.. include:: /etc/passwd\n\n.. raw:: html\n\n   <script>bad()</script>'))
        for expected in ['Visible HTML content.', '<table', '<th', 'href="#target"']:
            self.assertIn(expected, rendered)
        for forbidden in ['Hidden print content.', '<script>', 'root:', 'Unknown directive', 'Unknown interpreted text role']:
            self.assertNotIn(forbidden, rendered)


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False)
class DocumentFormatTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp
    admin_payload = native_tests.NativeDatatrackerTests.admin_payload
    create_doc = native_tests.NativeDatatrackerTests.create_doc

    def pdf(self):
        from pypdf import PdfWriter
        stream = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(stream)
        return SimpleUploadedFile('alternative.pdf', stream.getvalue())

    def word(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('word/document.xml', '<document><p>Word alternative</p></document>')
        return SimpleUploadedFile('alternative.docx', stream.getvalue())

    def values(self, doc, additions=2):
        url = f'/admin/doc/document/{doc.pk}/change/'
        values = self.admin_payload(url, {'name': doc.name, 'title': doc.title, 'type': doc.type_id, 'group': self.root.pk, 'comment_about_changes': 'Add alternative formats'})
        values.update({'tida_formats-TOTAL_FORMS': str(additions), 'tida_formats-INITIAL_FORMS': '0',
            'tida_formats-0-kind': 'pdf', 'tida_formats-0-upload': self.pdf()})
        if additions == 2:
            values.update({'tida_formats-1-kind': 'docx', 'tida_formats-1-upload': self.word()})
        return url, values

    def test_admin_can_attach_both_formats_and_public_downloads_are_revision_specific(self):
        doc = self.create_doc()
        url, values = self.values(doc)
        response = self.client.post(url, values)
        self.assertEqual(response.status_code, 302, response.context and response.context['inline_admin_formsets'][-1].formset.errors)
        alternatives = list(doc.tida_formats.all())
        self.assertEqual(len(alternatives), 2)
        self.assertTrue(all(row.revision == doc.rev for row in alternatives))
        self.assertTrue(all('content' in row.get_deferred_fields() for row in alternatives))
        self.client.logout()
        page = self.client.get(f'/doc/{doc.name}/')
        for row in alternatives:
            path = f'/document-formats/{row.pk}/download/'
            self.assertContains(page, path)
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF' if row.kind == 'pdf' else b'PK'))
        self.client.force_login(self.user)
        values = self.admin_payload(url, {'name': doc.name, 'title': doc.title, 'type': doc.type_id,
            'group': self.root.pk, 'comment_about_changes': 'Replace Word and remove PDF'})
        values.update({'tida_formats-TOTAL_FORMS': '2', 'tida_formats-INITIAL_FORMS': '2'})
        for index, row in enumerate(alternatives):
            values.update({f'tida_formats-{index}-id': row.pk, f'tida_formats-{index}-kind': row.kind})
            if row.kind == 'pdf':
                values[f'tida_formats-{index}-DELETE'] = 'on'
            else:
                values[f'tida_formats-{index}-upload'] = self.word()
        self.assertEqual(self.client.post(url, values).status_code, 302)
        alternatives = list(doc.tida_formats.all())
        self.assertEqual([row.kind for row in alternatives], ['docx'])
        from . import core
        core.save_document(doc, core.actor_for(self.user), 'Next revision', data=b'New source', filename='source.txt')
        latest = self.client.get(f'/doc/{doc.name}/')
        old = self.client.get(f'/doc/{doc.name}/revision/0.0/')
        for row in alternatives:
            self.assertNotContains(latest, f'/document-formats/{row.pk}/download/')
            self.assertContains(old, f'/document-formats/{row.pk}/download/')
        doc.tags.add('tida-uncatalogued')
        self.assertEqual(self.client.get(f'/document-formats/{alternatives[0].pk}/download/').status_code, 404)

    def test_duplicate_or_mismatched_formats_are_rejected_without_writing(self):
        doc = self.create_doc()
        url, values = self.values(doc)
        values['tida_formats-1-kind'] = 'pdf'
        values['tida_formats-1-upload'] = self.pdf()
        self.assertContains(self.client.post(url, values), 'Keep at most one PDF and one Word alternative per revision.')
        self.assertFalse(doc.tida_formats.exists())
        url, values = self.values(doc, 1)
        values['tida_formats-0-upload'] = self.word()
        self.assertContains(self.client.post(url, values), 'Select PDF for a .pdf file, or Word for a .docx file.')
        self.assertFalse(doc.tida_formats.exists())

    def test_incubation_is_public_catalogue_with_scoped_metadata_and_separate_submission(self):
        from .models import Contribution
        from ietf.group.models import Group
        doc = self.create_doc()
        inc = Group.objects.get(acronym='incubation')
        Contribution.objects.create(submitter=self.user, group=inc, document=doc, title=doc.title,
            content=b'Private submission storage', filename='contribution.txt', review_note='Private editorial feedback')
        hidden = Document.objects.create(name='inc-hidden', title='Hidden incubation item', group=inc, type_id='tida')
        hidden.tags.add('tida-uncatalogued')
        Document.objects.create(name='unrelated-public', title='Unrelated public item', group=self.root, type_id='tida')
        self.client.logout()
        page = self.client.get('/')
        self.assertContains(page, '/work/incubation/')
        response = self.client.get('/work/incubation/')
        self.assertEqual(response.status_code, 200)
        for expected in [doc.title, 'Formats / resources', 'Updated', '/submit/?group=incubation', 'not a working group']:
            self.assertContains(response, expected)
        for excluded in ['Hidden incubation item', 'Unrelated public item', 'Private submission storage', 'Private editorial feedback', self.user.email]:
            self.assertNotContains(response, excluded)
        self.assertEqual(self.client.get('/submit/?group=incubation').status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get('/submit/?group=incubation')
        self.assertEqual(response.context['form'].initial['group'].acronym, 'incubation')
        self.assertContains(response, 'not a working group')
