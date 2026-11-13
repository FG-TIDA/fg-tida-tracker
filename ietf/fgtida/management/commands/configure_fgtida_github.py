from django.core.management.base import BaseCommand
from django.core.management import call_command
from ietf.fgtida.models import GitHubAccount, WorkingGroupConfiguration, GitHubRepository
from ietf.group.models import Group
from django.db import transaction

GROUPS = [
    (0, 'Use Cases and Terminology', 'uct', 'https://github.com/fg-tida-wg0/wg0-uct-charter'),
    (1, 'Continuous and Dynamic Remote Attestation for Agentic AI', 'cdra', 'https://github.com/fg-tida-wg1/wg1-cdra-charter'),
    (2, 'Identity, Behaviour and Enforcement for AI Agents', 'ibea', 'https://github.com/fg-tida-wg2/wg2-ibea-charter'),
]


class Command(BaseCommand):
    help = 'Initialize editable WG/GitHub mappings. Existing mappings and manual topic names are preserved.'

    @transaction.atomic
    def handle(self, *args, **options):
        call_command('configure_fgtida', skip_checks=True)
        root = Group.objects.get(acronym='tida')
        account, _ = GitHubAccount.objects.get_or_create(organization='FG-TIDA')
        for number, topic, acronym, reference in GROUPS:
            group = Group.objects.get(acronym=f'tidawg{number}')
            owner, _ = GitHubAccount.objects.get_or_create(organization=f'fg-tida-wg{number}')
            config, created = WorkingGroupConfiguration.objects.get_or_create(group=group,
                defaults={'number':number, 'topic_acronym':acronym, 'github_account':owner})
            if created or group.name.strip()==f'WG{number}':
                group.name = topic
                if not group.description or group.description.strip() in {'WG1','WG2','WG0'}:
                    group.description = f'FG-TIDA WG{number}: {topic}. GitHub source: {reference}. Charter decisions remain with FG-TIDA editors.'
                group.save()
        sources = [
            (account,'themes','tida','issues','themes','tida',[]),
            (GitHubAccount.objects.get(organization='fg-tida-wg0'),'use-cases','tidawg0','issues','use-cases','tida',[]),
            (account,'meetings','tida','files','','slides',['20*/*.pptx','20*/*.pdf','20*/*.docx','20*/minutes.md','20*/agenda.md']),
            (account,'marketing','tida','files','','slides',['*.pptx','*.pdf']),
            (account,'spec-template','tida','files','templates','tida-template',['src/doc/index.rst']),
            (account,'wg-charter-template','tida','files','templates','tida-template',['src/doc/index.rst']),
        ]
        for number, repo, path in [(0,'wg0-uct-charter','charter.md'),(1,'wg1-cdra-charter','charter.md'),(2,'wg2-ibea-charter','src/doc/index.rst')]:
            owner = GitHubAccount.objects.get(organization=f'fg-tida-wg{number}')
            sources.append((owner,repo,f'tidawg{number}','files','','charter',[path]))
        for owner, name, acronym, mode, category, kind, paths in sources:
            from ietf.fgtida.classification import SOURCE_RULES
            _, _, group_evidence, category_evidence = SOURCE_RULES[(owner.organization+'/'+name).lower()]
            GitHubRepository.objects.get_or_create(account=owner, name=name, defaults={
                'group':Group.objects.get(acronym=acronym), 'mode':mode, 'category':category,
                'document_type':kind, 'paths':paths, 'group_evidence':group_evidence, 'category_evidence':category_evidence})
        # Retire only the empty, unconfigured WG3 placeholder from the earlier
        # setup. Real documents, memberships, submissions or custom names prevent
        # this automatic cleanup; future numbered WGs remain fully supported.
        old=Group.objects.filter(acronym='tidawg3',name='WG3',parent=root).first()
        if old and old.state_id!='conclude':
            from ietf.meeting.models import Session
            from ietf.fgtida.models import Contribution, ContributionRecipient
            from ietf.group.models import Role, GroupMilestone, GroupEvent
            if not any([old.document_set.exists(),Session.objects.filter(group=old).exists(),
                    Role.objects.filter(group=old).exists(),GroupMilestone.objects.filter(group=old).exists(),
                    Group.objects.filter(parent=old).exists(),Contribution.objects.filter(group=old).exists(),
                    ContributionRecipient.objects.filter(group=old).exists(),WorkingGroupConfiguration.objects.filter(group=old).exists()]):
                from ietf.group.utils import save_group_in_history
                from ietf.fgtida.core import actor_for
                save_group_in_history(old)
                old.state_id='conclude'
                old.save(update_fields=['state'])
                GroupEvent.objects.create(group=old,by=actor_for(),type='info_changed',desc='Unused placeholder retired after configuring WG0, WG1 and WG2. No documents or memberships were removed.')
        self.stdout.write('Editable GitHub sources and numbered topic groups configured. No GitHub writes or emails were sent.')
