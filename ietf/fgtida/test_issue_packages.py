from django.test import SimpleTestCase, override_settings
from .issue_packages import prepare_issue
from .source_packages import unpack


@override_settings(FGTIDA_UPLOAD_SCAN_REQUIRED=False)
class IssuePackageTests(SimpleTestCase):
    def test_quoted_identity_version_and_inline_description_are_preserved(self):
        original = (b'# Example use case\n\n> **Version:** v0.5\n> **Case owner:** Example Owner\n'
                    b'> **Contributors:** Example Coauthor; Another Coauthor\n\n## 1. Identification\n\n'
                    b'Submitting organization: Example Institute\n\n## 2. The situation\n\n'
                    b'**Plain language description** *(required, 3 sentences max)*: An agent checks its authority.\n\n'
                    b'## Details\n\nThe original technical text.\n')
        archive, metadata = prepare_issue(original, title='Example use case',
            credits={'authors': ['Example Coauthor; Another Coauthor']})
        files = unpack(archive)
        self.assertEqual(files['original/github-issue.md'], original)
        self.assertEqual(metadata['authors'], ['Example Owner', 'Example Coauthor', 'Another Coauthor'])
        self.assertEqual(metadata['affiliations'], ['Example Institute'])
        self.assertEqual(metadata['template_adaptation']['version'], '0.5')
        self.assertIn(b'An agent checks its authority.', files['index.rst'])
        self.assertIn(b'Version: 0.5', files['index.rst'])
        self.assertTrue(metadata['template_adaptation']['provisional'])
        self.assertTrue(all('Keywords' in issue for issue in metadata['template_adaptation']['issues']))

    def test_description_with_colon_inside_bold_marker(self):
        original = b'# Example\n\n**Plain language description:** A source-provided description.\n'
        archive, metadata = prepare_issue(original, title='Example', credits={})
        self.assertNotIn('Complete Abstract', str(metadata['template_adaptation']['issues']))
        self.assertIn(b'A source-provided description.', unpack(archive)['index.rst'])
