"""Export an explicit FG-TIDA graph without imported accounts or IETF records."""
import json
from collections import deque
from pathlib import Path
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction
from ietf.fgtida import core


class Command(BaseCommand):
    help = 'Export native FG-TIDA data and one approved account into a private release fixture.'

    def add_arguments(self, parser):
        parser.add_argument('--administrator', required=True)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        user = get_user_model().objects.get(username=options['administrator'], is_active=True, is_superuser=True)
        docs = set(core.documents(include_uncatalogued=True).values_list('pk', flat=True))
        groups = set(core.groups().values_list('pk', flat=True))
        meetings = set(core.meetings().values_list('pk', flat=True))
        sessions = set(apps.get_model('meeting', 'Session').objects.filter(meeting_id__in=meetings).values_list('pk', flat=True))
        events = set(apps.get_model('doc', 'DocEvent').objects.filter(doc_id__in=docs).values_list('pk', flat=True))
        queue = deque([user])
        from ietf.fgtida.models import Contribution, SessionResource, SessionResourceChunk, RecoveryQuestions, AdministratorApproval, SecurityQuestion
        from django_otp.plugins.otp_totp.models import TOTPDevice
        contributions = Contribution.objects.filter(group_id__in=groups)
        approved_ids = {user.pk} | set(AdministratorApproval.objects.values_list('user_id', flat=True))
        account_ids = approved_ids | set(contributions.values_list('submitter_id', flat=True)) | set(RecoveryQuestions.objects.values_list('user_id', flat=True))
        extra_accounts = get_user_model().objects.filter(pk__in=account_ids).exclude(pk=user.pk)
        if extra_accounts.exclude(pk__in=approved_ids).filter(models.Q(is_staff=True) | models.Q(is_superuser=True)).exists():
            raise CommandError('A contributor has privileged access; explicitly review the account policy before exporting.')
        queue.extend(extra_accounts)
        queue.extend(apps.get_model('person', 'Person').objects.filter(user_id__in=account_ids))
        queue.extend(apps.get_model('person', 'Email').objects.filter(person__user_id__in=account_ids))
        queue.extend(RecoveryQuestions.objects.filter(user_id__in=account_ids))
        queue.extend(SecurityQuestion.objects.all())
        queue.extend(TOTPDevice.objects.filter(user_id__in=account_ids, confirmed=True))
        # Recovery-code hashes are tied to this environment's signing key.
        # Generate fresh recovery codes after moving to a new production key.
        queue.extend(AdministratorApproval.objects.filter(user_id__in=account_ids))
        queue.extend(contributions)
        # Keep durable contribution notifications and their original attachments.
        # Password-reset and other environment-specific account mail is excluded.
        queue.extend(apps.get_model('fgtida', 'OutgoingMail').objects.filter(contribution__in=contributions))
        queue.extend(apps.get_model('fgtida', 'ContributionRecipient').objects.filter(group_id__in=groups))
        queue.extend(SessionResource.objects.filter(session_id__in=sessions))
        queue.extend(apps.get_model('fgtida', 'DocumentListing').objects.filter(document_id__in=docs))
        queue.extend(apps.get_model('person', 'Person').objects.filter(user=user))
        for records in (core.documents(include_uncatalogued=True), core.groups(), core.meetings()):
            queue.extend(records)
        # Include ownership/history relations, not unrelated reverse relations.
        for model in apps.get_models():
            app = model._meta.app_label
            if app not in {'doc', 'group', 'meeting'} or model._meta.proxy:
                continue
            condition = models.Q(pk__in=[])
            for field in model._meta.fields:
                if not field.is_relation or not field.many_to_one and not field.one_to_one:
                    continue
                label = field.related_model._meta.label_lower
                identifiers = {'doc.document': docs, 'group.group': groups,
                               'meeting.meeting': meetings, 'meeting.session': sessions,
                               'doc.docevent': events}.get(label)
                if identifiers is not None:
                    condition |= models.Q(**{field.attname + '__in': identifiers})
            queue.extend(model.objects.filter(condition))
        objects = {}
        with transaction.atomic():
            while queue:
                obj = queue.popleft()
                key = (obj._meta.label_lower, obj.pk)
                if key in objects:
                    continue
                if key[0] == 'auth.user' and obj.pk not in account_ids:
                    continue
                if key[0] in {'auth.group', 'auth.permission', 'sessions.session'}:
                    continue
                scope = {'doc.document': docs, 'group.group': groups, 'meeting.meeting': meetings}.get(key[0])
                if scope is not None and obj.pk not in scope:
                    raise CommandError(f'Release data references an out-of-scope {key[0]}; review the relationship first.')
                objects[key] = obj
                if len(objects) > 20000:
                    raise CommandError('Unexpectedly large export graph; refusing to export unrelated data.')
                for field in obj._meta.fields:
                    if field.is_relation and (field.many_to_one or field.one_to_one) and getattr(obj, field.attname) is not None:
                        related = getattr(obj, field.name)
                        if related._meta.label_lower == 'auth.user' and related.pk not in account_ids:
                            if not field.null:
                                raise CommandError('A required relation references an unapproved account.')
                            # In-memory copy only; the source database is unchanged.
                            setattr(obj, field.attname, None)
                        else:
                            queue.append(related)
                for field in obj._meta.many_to_many:
                    if field.remote_field.through._meta.auto_created and field.related_model._meta.app_label != 'auth':
                        queue.extend(getattr(obj, field.name).all())
            data = json.loads(serializers.serialize('json', objects.values()))
            for record in data:
                if record['model'] == 'auth.user':
                    record['fields']['groups'] = []
                    record['fields']['user_permissions'] = []
        output = Path(options['output'])
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as target:
            target.write('[')
            first = True
            for record in data:
                if not first:
                    target.write(',')
                json.dump(record, target, ensure_ascii=False)
                first = False
            # Stream binary chunks; never accumulate recordings in the export
            # graph or serialize the complete media collection in memory.
            for chunk in SessionResourceChunk.objects.filter(resource__session_id__in=sessions).iterator(chunk_size=1):
                if not first:
                    target.write(',')
                target.write(serializers.serialize('json', [chunk])[1:-1])
                first = False
            target.write(']')
        output.chmod(0o600)
        self.stdout.write(self.style.SUCCESS(f'Exported {len(data)} records, with one approved administrator and {len(account_ids) - 1} contributor accounts. Protect this fixture as confidential; it contains account password hashes and private submissions.'))
