from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms import modelform_factory
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from . import core, tests as native_tests
from .credit_lists import parse_credit_list, format_credit_list
from .github_attribution import source_attribution
from .models import Contribution, DocumentAttribution, GitHubAccount, GitHubRepository, GitHubSource
from .source_packages import pack


class CreditListTests(SimpleTestCase):
    def test_multiple_values_quotes_and_new_lines_round_trip(self):
        values = parse_credit_list('First Author, "Family, Given"\nThird Author, First Author')
        self.assertEqual(values, ['First Author', 'Family, Given', 'Third Author'])
        self.assertEqual(parse_credit_list(format_credit_list(values)), values)

    def test_source_multiple_proposers_are_not_misclassified_as_affiliations(self):
        for body in ['**Authors:** First Author, Second Author, Third Author',
                     '## Proposer(s)\n\nFirst Author\nSecond Author\nThird Author',
                     '## Proposer(s)\n\n- First Author\n- Second Author\n- Third Author\n\n## Summary\nBody']:
            credits = source_attribution(body)
            self.assertEqual(credits['authors'], ['First Author', 'Second Author', 'Third Author'])
            self.assertFalse(credits.get('affiliations'))
        values = source_attribution('| Theme proposers | First Author, Second Author (Example Lab) |')
        self.assertEqual(values['authors'], ['First Author', 'Second Author'])
        self.assertEqual(values['affiliations'], ['Example Lab'])
        declared = source_attribution('## Proposer(s)\n<!-- Name and affiliation -->\nFirst Author, Example Lab\nSecond Author, Example Lab')
        self.assertEqual(declared['authors'], ['First Author', 'Second Author'])
        self.assertEqual(declared['affiliations'], ['Example Lab'])

    def test_combined_proposer_biographies_do_not_create_fake_authors(self):
        samples = [
            ('First Person, <first@example.test>, Example Technologies GmbH, Example City, Example Country\nSecond Person, <second@example.test>, Example Institute, Other City, Other Country', ['First Person', 'Second Person']),
            ('First Person, Company Example LLC (Example Country)', ['First Person']),
            ('First Person (Example Lab), proposer of another theme. Comments from everyone welcome.', ['First Person']),
            ('First Person\nAI Expert (Individual, Example Country)\nMBA (Other Country)\nSection Leader for a Program, Example University\nInvited Expert to another group', ['First Person']),
            ('First Person, Esq., Managing Director, Example Company', ['First Person']),
            ('First Person\nIN Groupe', ['First Person']),
        ]
        for value, expected in samples:
            with self.subTest(value=value):
                result = source_attribution('## Proposer(s)\n\n' + value + '\n\n## Summary\nBody')
                self.assertEqual(result['authors'], expected)

    def test_source_author_list_does_not_truncate_at_twenty_people(self):
        names = [f'Example Author {number}' for number in range(35)]
        self.assertEqual(source_attribution('**Authors:** ' + ', '.join(names))['authors'], names)


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=False)
class CoauthorMetadataTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def create_submission(self):
        from ietf.group.models import Group
        self.member = get_user_model().objects.create_user('recorded-owner', first_name='First', last_name='Author')
        self.client.force_login(self.member)
        response = self.client.post('/submit/', {'title': 'Coauthored work item', 'group': Group.objects.get(acronym='tidawg1').pk,
            'meeting': native_tests.open_submission_meeting().pk, 'submission_method': 'rst', 'entry_document': 'index.rst',
            'publication_consent': 'on', 'authors': 'First Author, Second Author, Third Author',
            'affiliations': 'Example Lab, "Institute, Paris"',
            'upload': SimpleUploadedFile('source.zip', pack({'index.rst': b'Original source content.'}))})
        self.assertEqual(response.status_code, 302)
        return Contribution.objects.get()

    def test_coauthors_need_no_accounts_and_submitting_account_stays_fixed(self):
        item = self.create_submission()
        metadata = item.document.tida_attribution
        self.assertEqual(metadata.authors.splitlines(), ['First Author', 'Second Author', 'Third Author'])
        self.assertEqual(metadata.affiliations.splitlines(), ['Example Lab', 'Institute, Paris'])
        self.assertEqual(item.submitter_id, self.member.pk)
        self.assertFalse(get_user_model().objects.filter(first_name='Second').exists())
        page = self.client.get(reverse('fgtida:document', args=[item.document.name]))
        self.assertEqual(len(page.context['author_entries']), 3)
        self.assertEqual(len(page.context['submitted_by']), 1)
        self.assertEqual(page.context['submitted_by'][0]['person_id'], core.actor_for(self.member).pk)

    def test_owner_can_update_coauthors_without_altering_original_or_ownership(self):
        item = self.create_submission()
        original = bytes(item.content)
        url = reverse('fgtida:contribution-authors', args=[item.pk])
        response = self.client.post(url, {'authors': 'First Author, "Family, Given", Fourth Author',
            'affiliations': 'Example Lab, "Institute, Paris"'})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.submitter_id, self.member.pk)
        self.assertEqual(bytes(item.content), original)
        self.assertEqual(item.document.tida_attribution.authors.splitlines(), ['First Author', 'Family, Given', 'Fourth Author'])
        self.assertIn('"Family, Given"', self.client.get(url).context['form']['authors'].value())
        self.assertEqual(self.client.post(url, {'authors': 'Other', 'submitter': self.user.pk}).status_code, 403)
        stranger = get_user_model().objects.create_user('another-member')
        self.client.force_login(stranger)
        self.assertEqual(self.client.post(url, {'authors': 'Other'}).status_code, 404)
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(url, {'authors': '', 'affiliations': ''}).status_code, 302)
        self.assertEqual(self.client.get(url).context['form']['authors'].value(), '')

    def test_admin_forms_preserve_comma_inside_an_affiliation(self):
        from .admin import PublicDocumentMetadataForm
        item = self.create_submission()
        metadata = item.document.tida_attribution
        form_class = modelform_factory(DocumentAttribution, form=PublicDocumentMetadataForm, fields=['authors', 'affiliations'])
        initial = form_class(instance=metadata)
        bound = form_class(instance=metadata, data={field: initial[field].value() for field in ('authors', 'affiliations')})
        self.assertTrue(bound.is_valid(), bound.errors)
        bound.save()
        metadata.refresh_from_db()
        self.assertEqual(metadata.affiliations.splitlines(), ['Example Lab', 'Institute, Paris'])

    def test_admin_can_correct_recorded_submitter_without_changing_authors(self):
        item = self.create_submission()
        replacement = get_user_model().objects.create_user('corrected-owner', first_name='Correct', last_name='Submitter')
        self.client.force_login(self.user)
        response = self.client.post(f'/admin/fgtida/contribution/{item.pk}/change/', {
            'sequence_number': item.sequence_number, 'submitter': replacement.pk, 'review_note': 'Correct recorded submitting account.'})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.submitter_id, replacement.pk)
        self.assertEqual(item.document.tida_attribution.authors.splitlines(), ['First Author', 'Second Author', 'Third Author'])
        page = self.client.get(reverse('fgtida:document', args=[item.document.name]))
        self.assertEqual(page.context['submitted_by'][0]['person_id'], core.actor_for(replacement).pk)

    def test_admin_contact_email_edit_does_not_change_the_coauthor_list(self):
        item = self.create_submission()
        self.client.force_login(self.user)
        response = self.client.post(f'/admin/fgtida/contribution/{item.pk}/change/', {
            'sequence_number': item.sequence_number, 'contact_email': 'contact@example.test', 'review_note': 'Correct contact address only.'})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.document.tida_attribution.authors.splitlines(), ['First Author', 'Second Author', 'Third Author'])
        self.assertEqual(item.document.tida_attribution.affiliations.splitlines(), ['Example Lab', 'Institute, Paris'])

    def test_git_source_updates_keep_all_authors_and_preserve_admin_overrides(self):
        from .github_catalog import store_source
        from .catalog_repairs import repair_catalog
        account = GitHubAccount.objects.create(organization='FG-TIDA')
        repository = GitHubRepository.objects.create(account=account, name='themes', group=self.root, mode='issues')
        args = (repository, 'issue:101')
        url = repository.url + '/issues/101'
        store_source(*args, b'# A theme\n\n**Authors:** First Author\n', 'issue-101.md', 'A theme', url, 'old')
        source = GitHubSource.objects.get(repository=repository)
        metadata = DocumentAttribution.objects.create(document=source.document, authors='First Author')
        store_source(*args, b'# A theme\n\n**Authors:** First Author, Second Author\n', 'issue-101.md', 'A theme', url, 'new')
        page_url = reverse('fgtida:document', args=[source.document.name])
        self.assertEqual(self.client.get(page_url).context['credits']['authors'], ['First Author', 'Second Author'])
        repair_catalog(apply=True)
        metadata.refresh_from_db()
        self.assertEqual(metadata.authors.splitlines(), ['First Author', 'Second Author'])
        metadata.authors = 'Administrator Corrected Author'
        metadata.credit_provenance = {'authors': {'mode': 'manual'}}
        metadata.save()
        store_source(*args, b'# A theme\n\n**Authors:** First Author, Second Author, Third Author\n', 'issue-101.md', 'A theme', url, 'newer')
        self.assertEqual(self.client.get(page_url).context['credits']['authors'], ['Administrator Corrected Author'])
