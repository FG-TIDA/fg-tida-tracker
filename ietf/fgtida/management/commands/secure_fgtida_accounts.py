import json
from pathlib import Path
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q


class Command(BaseCommand):
    help = 'Keep one explicitly selected administrator; back up and revoke other privileged accounts.'

    def add_arguments(self, parser):
        parser.add_argument('--administrator', required=True)
        parser.add_argument('--backup', required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        users = get_user_model().objects
        selected = users.filter(username=options['administrator'], is_active=True).first()
        if not selected or not selected.has_usable_password():
            raise CommandError('Selected account must exist, be active and have a usable password.')
        others = users.exclude(pk=selected.pk).filter(Q(is_staff=True) | Q(is_superuser=True))
        self.stdout.write(f'{others.count()} other privileged accounts would lose access.')
        if not options['apply']:
            return
        backup = Path(options['backup'])
        backup.parent.mkdir(parents=True, exist_ok=True)
        with transaction.atomic():
            records = list(users.select_for_update().filter(Q(pk=selected.pk) | Q(pk__in=others.values('pk')))
                           .values('pk', 'username', 'is_active', 'is_staff', 'is_superuser'))
            with backup.open('x', encoding='utf-8') as target:
                json.dump(records, target)
            backup.chmod(0o600)
            others.update(is_active=False, is_staff=False, is_superuser=False)
            users.filter(pk=selected.pk).update(is_staff=True, is_superuser=True)
            # Old signed sessions must not preserve access; the administrator signs in again.
            Session.objects.all().delete()
        self.stdout.write(self.style.SUCCESS('Only the selected account retains privileged access. Existing sessions were invalidated.'))
