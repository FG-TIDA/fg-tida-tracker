from io import StringIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from ietf.person.models import Person, Email
from . import core, tests as native_tests
from .models import Contribution, EmailVerification, OutgoingMail
from .contributions import can_revise
from .source_packages import pack


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False, FGTIDA_UPLOAD_SCAN_REQUIRED=False,
                   FGTIDA_MAIL_ENABLED=False, FGTIDA_MAIL_IMMEDIATE=False)
class SubmissionAccessTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp
    source_submission = native_tests.NativeDatatrackerTests.source_submission

    def original(self):
        from ietf.group.models import Group
        payload = {'title': 'Baseline Terminology for Trusted AI Agent Identity - original proposal',
                   'group': Group.objects.get(acronym='tidawg0').pk, **self.source_submission()}
        self.assertEqual(self.client.post('/submit/', payload).status_code, 302)
        item = Contribution.objects.select_related('document').get()
        item.contact_email = 'contact@example.test'
        item.save(update_fields=['contact_email'])
        return item

    def contact(self, verified=True):
        user = get_user_model().objects.create_user('verified-contact', email='CONTACT@example.test', password='fixture-password')
        EmailVerification.objects.create(user=user, email=user.email, verified=timezone.now() if verified else None)
        self.client.force_login(user)
        return user

    def test_admin_contact_gains_access_only_after_verified_activation(self):
        item = self.original()
        user = self.contact(False)
        self.assertFalse(can_revise(item, user))
        self.assertEqual(self.client.get(f'/submit/{item.pk}/').status_code, 404)
        EmailVerification.objects.filter(user=user).update(verified=timezone.now())
        self.assertTrue(can_revise(item, user))
        self.assertContains(self.client.get('/submit/'), item.reference)
        self.assertEqual(self.client.get(f'/submit/{item.pk}/revision/').status_code, 200)
        self.assertContains(self.client.get(f'/doc/{item.document.name}/'), 'Submit a new version')
        from django.core.files.uploadedfile import SimpleUploadedFile
        revised = native_tests.template_source(b'Contact-authored revision.').replace(b'Version: 0.0', b'Version: 0.1')
        response = self.client.post(f'/submit/{item.pk}/revision/', {
            'submission_method': 'rst', 'version': '0.1', 'publication_consent': 'on',
            'entry_document': 'index.rst', 'upload': SimpleUploadedFile('revision.zip', pack({'index.rst': revised}))})
        self.assertEqual(response.status_code, 302, response.context and response.context['form'].errors)
        item.refresh_from_db()
        self.assertEqual(item.document.tida_versions.order_by('-recorded').first().version, '0.1')
        self.assertEqual(item.submitter_id, self.user.pk)  # Original provenance is retained.
        user.is_active = False
        self.assertFalse(can_revise(item, user))

    def test_contact_can_register_without_stealing_existing_person_or_account(self):
        item = self.original()
        person = Person.objects.create(name='Contact without account', ascii='Contact without account')
        email = Email.objects.create(address=item.contact_email, person=person, origin='FG-TIDA administration')
        from .accounts import is_retired_account_email
        self.assertTrue(is_retired_account_email(email))
        self.client.logout()
        response = self.client.post('/accounts/create/', {'email': item.contact_email})
        self.assertEqual(response.status_code, 302)
        email.refresh_from_db()
        self.assertEqual(email.person_id, person.pk)
        self.assertFalse(get_user_model().objects.filter(email=item.contact_email).exists())
        other = get_user_model().objects.create_user('already-registered', email=item.contact_email)
        person.user = other; person.save()
        self.assertFalse(is_retired_account_email(Email.objects.select_related('person').get(pk=email.pk)))

    def test_resubmission_of_owned_title_creates_no_record_or_mail(self):
        item = self.original()
        self.contact()
        before = OutgoingMail.objects.count()
        payload = {'title': 'Baseline Terminology for Trusted AI Agent Identity', 'group': item.group_id,
                   **self.source_submission(b'Revised text, completed abstract and keywords.')}
        response = self.client.post('/submit/', payload)
        self.assertContains(response, 'No new record was created')
        self.assertEqual(Contribution.objects.count(), 1)
        self.assertEqual(OutgoingMail.objects.count(), before)

    def test_identical_public_file_from_another_account_is_not_a_new_item(self):
        item = self.original()
        self.contact()
        payload = {'title': 'A different title for the same uploaded file', 'group': item.group_id,
                   **self.source_submission()}
        response = self.client.post('/submit/', payload)
        self.assertContains(response, 'No new record was created')
        self.assertEqual(Contribution.objects.count(), 1)

    def test_consolidation_keeps_originals_and_uses_new_editorial_archive(self):
        original = self.original()
        # Model creation simulates an existing production duplicate, bypassing the new form guard.
        from ietf.doc.models import Document, State
        archive = pack({'index.rst': native_tests.template_source(b'Corrected technical content.')})
        duplicate_doc = Document(name='duplicate-preserved-fixture', title=original.title, type_id='tida', group=original.group)
        core.save_document(duplicate_doc, core.actor_for(), 'Author resubmission', data=archive, filename='source.zip', version='0.0', state=State.objects.get(type_id='tida', slug='submitted'))
        duplicate = Contribution.objects.create(title=original.title, group=original.group, document=duplicate_doc,
            filename='source.zip', content=archive, source_archive=archive, source_manifest={'method': 'rst'}, contact_email=original.contact_email)
        output = StringIO()
        call_command('review_fgtida_duplicates', primary=original.pk, duplicate=duplicate.pk, stdout=output)
        original.document.refresh_from_db()
        self.assertEqual(original.document.rev, '00')
        mail_count = OutgoingMail.objects.count()
        with patch('ietf.fgtida.contributions.queue_notifications', side_effect=AssertionError('No merge notifications')):
            call_command('review_fgtida_duplicates', primary=original.pk, duplicate=duplicate.pk,
                apply=True, reason='Same author confirmed this is the corrected original submission.', stdout=output)
        duplicate.refresh_from_db(); original.document.refresh_from_db()
        self.assertEqual(duplicate.duplicate_of_id, original.pk)
        self.assertEqual(Contribution.objects.count(), 2)
        self.assertEqual(duplicate_doc.tida_versions.count(), 1)
        self.assertEqual(original.document.tida_versions.count(), 2)
        self.assertEqual(OutgoingMail.objects.count(), mail_count)
        self.assertNotIn(duplicate_doc.pk, core.documents().values_list('pk', flat=True))
        self.assertEqual(self.client.get(f'/doc/{duplicate_doc.name}/').status_code, 302)
        self.assertEqual(self.client.get(f'/doc/{duplicate_doc.name}/history/').status_code, 200)
        self.assertEqual(self.client.get(f'/doc/{duplicate_doc.name}/download/0.0/').status_code, 200)
        self.assertEqual(original.revision_originals.count(), 1)
