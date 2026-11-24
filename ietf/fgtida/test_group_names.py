from importlib import import_module
from django.apps import apps
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from ietf.group.models import Group
from ietf.meeting.models import Meeting, Session
from . import tests as native_tests
from .models import WorkingGroupConfiguration, GitHubAccount
from .templatetags.fgtida import group_label


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False)
class WorkingGroupNameTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def test_names_migrate_without_changing_ids_or_connections_and_stay_updated(self):
        from datetime import date
        from django.core.management import call_command
        from io import StringIO
        migration = import_module('ietf.fgtida.migrations.0046_update_working_group_names')
        originals = {}
        for number, old, acronym in [(0,'Use Cases and Terminology','uct'),
            (1,'Continuous and Dynamic Attestation for Agentic AI','cd3a'), (5,'ICAP','icap')]:
            group, _ = Group.objects.get_or_create(acronym=f'tidawg{number}', defaults={
                'parent':self.root,'type_id':'wg','state_id':'active','name':old})
            Group.objects.filter(pk=group.pk).update(name=old)
            config, _ = WorkingGroupConfiguration.objects.get_or_create(group=group, defaults={'number':number,'topic_acronym':acronym})
            config.github_account = GitHubAccount.objects.create(organization=f'example-existing-wg{number}')
            config.save(update_fields=['github_account'])
            originals[number] = (group.pk, config.github_account_id)
        meeting = Meeting.objects.create(number='rename-test',type_id='tida',date=date(2026,11,30),days=2)
        session = Session.objects.create(meeting=meeting,group=self.root,purpose_id='regular',type_id='session',
            name='Session 14 – WG5: ICAP',agenda_note='Use Cases and Terminology; Continuous and Dynamic Attestation for Agentic AI (CD3A)')
        migration.update_names(apps, connection.schema_editor())
        migration.update_names(apps, connection.schema_editor())
        call_command('configure_fgtida_github',skip_checks=True,stdout=StringIO())
        for number, name, acronym in migration.NAMES:
            group = Group.objects.get(pk=originals[number][0])
            self.assertEqual(group.name, name)
            self.assertEqual(group.tida_configuration.github_account_id, originals[number][1])
            self.assertEqual(group_label(group), f'WG{number}: {name} ({acronym.upper()})')
        session.refresh_from_db()
        self.assertIn('Implementation Conditions and Adoption Pathways for Trusted Agentic AI (ICAP)', session.name)
        self.assertIn('Taxonomy',session.agenda_note)
        self.assertIn('(CDRA)',session.agenda_note)
        self.assertIn('Remote Attestation',session.agenda_note)
        self.assertNotIn('CD3A',session.agenda_note)

    def test_wg5_is_removed_from_roster_but_retains_its_agenda_and_links(self):
        from datetime import date
        from django.core.management import call_command
        from io import StringIO
        group = Group.objects.create(acronym='tidawg5',parent=self.root,type_id='wg',state_id='active',name='ICAP')
        WorkingGroupConfiguration.objects.create(group=group,number=5,topic_acronym='icap')
        meeting = Meeting.objects.create(number='agenda-only-wg5',type_id='tida',date=date(2026,11,30),days=2)
        session = Session.objects.create(meeting=meeting,group=group,purpose_id='regular',type_id='session',name='WG5: Implementation Conditions and Adoption Pathways for Trusted Agentic AI (ICAP)')
        migration = import_module('ietf.fgtida.migrations.0047_remove_wg5_from_working_groups')
        migration.remove_from_roster(apps, connection.schema_editor())
        call_command('configure_fgtida',skip_checks=True,stdout=StringIO())
        group.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(group.type_id,'team')
        self.assertEqual(session.group_id,group.pk)
        self.assertIn('WG5:',session.name)
        self.assertNotContains(self.client.get(reverse('fgtida:groups')), 'Implementation Conditions and Adoption Pathways')
        self.assertNotIn('tidawg5',self.client.get('/submit/').context['form'].fields['group'].queryset.values_list('acronym',flat=True))
        self.assertNotContains(self.client.get('/'), 'Implementation Conditions and Adoption Pathways')
