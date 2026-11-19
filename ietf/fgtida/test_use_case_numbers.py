from importlib import import_module
from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from ietf.doc.models import Document
from ietf.group.models import Group
from . import tests as native_tests
from .document_types import assign_source_type
from .models import Contribution, ContributionSequence, DocumentAttribution, PublicDocumentType, OutgoingMail
from .templatetags.fgtida import document_type


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_MAIL_ENABLED=False)
class UseCaseNumberTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def test_home_and_document_search_share_all_types_and_new_admin_types(self):
        home = self.client.get('/')
        listing = self.client.get('/doc/')
        self.assertEqual(dict(home.context['types']), dict(listing.context['types']))
        labels = list(dict(home.context['types']).values())
        for label in ['Contribution', 'Use case', 'Theme', 'Template', 'Deliverable', 'Resource']:
            self.assertIn(label, labels)
        self.assertEqual(labels.count('Template'), 1)
        custom = PublicDocumentType.objects.create(name='Technical report')
        doc = self.document('custom-type-from-home')
        DocumentAttribution.objects.create(document=doc, public_type=custom)
        self.client.logout()
        home = self.client.get('/')
        key = f'public-{custom.pk}'
        self.assertContains(home, f'<option value="{key}"')
        self.assertContains(home, 'Technical report')
        self.assertContains(self.client.get('/doc/', {'type': key}), doc.title)

    def document(self, name):
        return Document.objects.create(name=name, title=name, type_id='tida', group=Group.objects.get(acronym='tidawg0'))

    def existing(self):
        doc = self.document('existing-contribution')
        return Contribution.objects.create(document=doc, group=doc.group, title=doc.title, content=b'', filename='', sequence_number=7)

    def backfill(self):
        import_module('ietf.fgtida.migrations.0039_number_wg0_use_cases').number_use_cases(apps, connection.schema_editor())

    def test_backfill_continues_existing_numbers_and_is_repeatable_without_mail(self):
        existing = self.existing()
        use_case = self.document('imported-use-case')
        use_case.tags.add('tida-use-cases')
        self.backfill()
        item = Contribution.objects.get(document=use_case)
        self.assertEqual(item.reference, 'FG-TIDA-WG0-008')
        self.assertEqual(item.status, 'catalogued')
        self.assertTrue(item.notifications_queued)
        self.assertEqual(document_type(Document.objects.get(pk=use_case.pk)), 'Contribution, Use case')
        self.backfill()
        self.assertEqual(Contribution.objects.filter(document=use_case).count(), 1)
        item.refresh_from_db()
        existing.refresh_from_db()
        self.assertEqual(item.sequence_number, 8)
        self.assertEqual(existing.sequence_number, 7)
        self.assertFalse(OutgoingMail.objects.exists())
        next_doc = self.document('next-submission')
        following = Contribution.objects.create(document=next_doc, group=next_doc.group, title=next_doc.title, content=b'', filename='')
        self.assertEqual(following.reference, 'FG-TIDA-WG0-009')

    def test_future_sync_reuses_document_and_number_and_both_filters_match(self):
        self.existing()
        doc = self.document('future-use-case')
        assign_source_type(doc, 'use-cases')
        item = Contribution.objects.get(document=doc)
        assign_source_type(doc, 'use-cases')
        self.assertEqual(Contribution.objects.filter(document=doc).count(), 1)
        self.assertEqual(item.sequence_number, 8)
        for name in ['Contribution', 'Use case']:
            kind = PublicDocumentType.objects.get(name=name)
            response = self.client.get('/doc/', {'type': f'public-{kind.pk}'})
            self.assertContains(response, doc.title)
            self.assertContains(response, 'FG-TIDA-WG0-008')
            self.assertContains(response, 'Contribution, Use case')
        page = self.client.get(f'/admin/doc/document/{doc.pk}/change/')
        fields = [inline.formset.form.base_fields for inline in page.context['inline_admin_formsets']]
        self.assertTrue(any('additional_types' in group for group in fields))

    def test_existing_contribution_and_primary_editor_type_are_preserved(self):
        item = self.existing()
        primary = PublicDocumentType.objects.get_or_create(name='Technical note')[0]
        DocumentAttribution.objects.create(document=item.document, public_type=primary)
        assign_source_type(item.document, 'use-cases')
        self.assertEqual(Contribution.objects.count(), 1)
        self.assertEqual(Contribution.objects.get().sequence_number, 7)
        self.assertEqual(set(document_type(Document.objects.get(pk=item.document_id)).split(', ')), {'Technical note', 'Contribution', 'Use case'})

    def test_stale_counter_does_not_skip_after_current_highest_number(self):
        self.existing()
        ContributionSequence.objects.filter(prefix='FG-TIDA-WG0').update(last_number=99)
        doc = self.document('stale-counter-use-case')
        assign_source_type(doc, 'use-cases')
        self.assertEqual(Contribution.objects.get(document=doc).sequence_number, 8)

    def test_github_issue_batch_numbers_oldest_first_and_does_not_duplicate_on_resync(self):
        from unittest.mock import Mock
        from .github_catalog import sync_repository
        from .models import GitHubAccount, GitHubRepository, GitHubSource
        self.existing()
        account = GitHubAccount.objects.create(organization='fg-tida-wg0')
        repo = GitHubRepository.objects.create(account=account, name='use-cases', mode='issues',
            group=Group.objects.get(acronym='tidawg0'), group_evidence='https://github.com/fg-tida-wg0/use-cases',
            category='use-cases', category_evidence='https://github.com/fg-tida-wg0/use-cases')
        issues = [{'number': n, 'title': f'Use case {n}', 'body': 'Example source text',
            'html_url': f'https://github.com/fg-tida-wg0/use-cases/issues/{n}', 'updated_at': f'2026-11-{n:02}'} for n in (1, 2)]
        client = Mock()
        client.pages.return_value = issues
        for _ in range(2):
            sync_repository(repo, client=client, metadata={'id': 123})
        client.pages.assert_called_with('/repos/fg-tida-wg0/use-cases/issues?state=all&sort=created&direction=asc')
        numbers = [Contribution.objects.get(document=GitHubSource.objects.get(repository=repo, source_key=f'issue:{n}').document).sequence_number for n in (1, 2)]
        self.assertEqual(numbers, [8, 9])
        self.assertEqual(Contribution.objects.count(), 3)
        self.assertFalse(OutgoingMail.objects.exists())
