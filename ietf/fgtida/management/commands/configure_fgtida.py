from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group as AuthGroup, Permission
from ietf.name.models import (GroupTypeName, GroupStateName, MeetingTypeName, DocTypeName, DocTagName,
    AgendaTypeName, AgendaFilterTypeName, SessionPurposeName, TimeSlotTypeName, RoleName, ImportantDateName, SessionStatusName)
from ietf.group.models import Group, GroupFeatures
from ietf.doc.models import StateType, State
from ietf.fgtida.core import CATEGORIES, TYPE_LABELS


class Command(BaseCommand):
    help = 'Configure FG-TIDA using native Datatracker vocabularies, groups and permissions.'

    def handle(self, *args, **options):
        from django.conf import settings
        from django.contrib.sites.models import Site
        domain = getattr(settings, 'FGTIDA_PUBLIC_DOMAIN', '')
        if domain:
            Site.objects.update_or_create(pk=settings.SITE_ID, defaults={'domain': domain, 'name': 'FG-TIDA Datatracker'})
            Site.objects.clear_cache()
        from ietf.fgtida.workflow import SESSION_STATES
        for slug, (label, color) in SESSION_STATES.items():
            SessionStatusName.objects.get_or_create(slug=slug, defaults={'name': label})
        AgendaTypeName.objects.get_or_create(slug='ietf', defaults={'name': 'Standard agenda'})
        AgendaFilterTypeName.objects.get_or_create(slug='none', defaults={'name': 'None'})
        for slug, label in [('active', 'Active'), ('proposed', 'Proposed'), ('conclude', 'Concluded')]:
            GroupStateName.objects.get_or_create(slug=slug, defaults={'name': label})
        for slug, label in [('fg', 'Focus Group'), ('wg', 'Working Group'), ('team', 'Team')]:
            kind, _ = GroupTypeName.objects.get_or_create(slug=slug, defaults={'name': label, 'verbose_name': label})
            GroupFeatures.objects.get_or_create(type=kind, defaults={'has_documents': True, 'has_meetings': True,
                'has_session_materials': True, 'has_milestones': True, 'is_schedulable': True, 'show_on_agenda': True,
                'acts_like_wg': slug == 'wg', 'default_used_roles': ['chair', 'secr'], 'session_purposes': ['regular']})
        for slug, label in [('chair', 'Chair'), ('secr', 'Secretariat'), ('member', 'Member')]:
            RoleName.objects.get_or_create(slug=slug, defaults={'name': label})
        TimeSlotTypeName.objects.get_or_create(slug='session', defaults={'name': 'Session'})
        SessionPurposeName.objects.get_or_create(slug='regular', defaults={'name': 'Regular session', 'timeslot_types': ['session']})
        MeetingTypeName.objects.get_or_create(slug='tida', defaults={'name': 'FG-TIDA Meeting'})
        for slug, label in [('contributions', 'Contribution deadline'), ('agenda', 'Agenda publication'),
                            ('registration', 'Registration deadline'), ('minutes', 'Minutes publication')]:
            ImportantDateName.objects.get_or_create(slug='tida-' + slug, defaults={'name': label, 'default_offset_days': 0})
        root, _ = Group.objects.get_or_create(acronym='tida', defaults={
            'name': 'Trust and Identity for Humans and Agentic AI', 'type_id': 'fg', 'state_id': 'active',
            'description': 'ITU-T Focus Group on Trust and Identity for Humans and Agentic AI.'})
        for acronym, name in [('tidawg1', 'WG1'), ('tidawg2', 'WG2'),
                              ('tidawg3', 'WG3'), ('incubation', 'Incubation')]:
            Group.objects.get_or_create(acronym=acronym, defaults={'name': name,
                'parent': root, 'type_id': 'team' if acronym == 'incubation' else 'wg',
                'state_id': 'active'})
        for slug, label in TYPE_LABELS:
            DocTypeName.objects.update_or_create(slug=slug, defaults={'name': label})
            kind, _ = StateType.objects.get_or_create(slug=slug, defaults={'label': 'Document state'})
            states = [('submitted', 'Submitted'), ('draft', 'Draft'), ('review', 'Under review'),
                      ('approved', 'Approved'), ('published', 'Published'), ('withdrawn', 'Withdrawn')] if slug == 'tida' else [('active', 'Active'), ('deleted', 'Deleted')]
            for order, (code, label) in enumerate(states):
                State.objects.get_or_create(type=kind, slug=code, defaults={'name': label, 'order': order})
        for slug, label in CATEGORIES:
            DocTagName.objects.update_or_create(slug='tida-' + slug, defaults={'name': label})
        DocTagName.objects.get_or_create(slug='tida-uncatalogued', defaults={'name': 'Excluded from FG-TIDA public catalog'})
        role, _ = AuthGroup.objects.get_or_create(name='FG-TIDA Administrators')
        from django.db.models import Q
        from ietf.fgtida.security import EDITOR_MODELS
        scope = Q(pk__in=[])
        for label in EDITOR_MODELS:
            app, model = label.split('.')
            scope |= Q(content_type__app_label=app, content_type__model=model,
                       codename__in=[f'{action}_{model}' for action in ('view', 'add', 'change')])
        scope |= Q(content_type__app_label='person', content_type__model__in=['person', 'email', 'alias'], codename__startswith='view_')
        permissions = Permission.objects.filter(scope)
        role.permissions.set(permissions)
        self.stdout.write('Native FG-TIDA configuration ready. No user permissions were granted automatically.')
