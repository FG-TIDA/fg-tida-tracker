from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from django.db.models import F
from django.db import connection
from django.conf import settings
import time
from ietf.fgtida.models import OutgoingMail
from ietf.fgtida.mail import deliver


class Command(BaseCommand):
    help = 'Retry up to 100 queued, unexpired FG-TIDA emails after SMTP is configured.'

    def handle(self, *args, **kwargs):
        if not settings.FGTIDA_MAIL_ENABLED:
            self.stdout.write('SMTP is disabled; queued messages were retained.')
            return
        # Shared PostgreSQL lock prevents overlapping cron runs across hosts.
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(746432, 1)')
            acquired = cursor.fetchone()[0]
        if not acquired:
            self.stdout.write('Another outbox delivery job is running.')
            return
        try:
            self.drain()
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(746432, 1)')

    def drain(self):
        from ietf.fgtida.models import Contribution
        from ietf.fgtida.contributions import queue_notifications
        for pk in Contribution.objects.filter(notifications_queued=False).values_list('pk', flat=True)[:100]:
            queue_notifications(pk)
        items = OutgoingMail.objects.filter(sent__isnull=True, attempts__lt=5).filter(
            Q(expires__isnull=True) | Q(expires__gt=timezone.now())).order_by(
                F('expires').asc(nulls_last=True), 'created')[:100]
        started = time.monotonic()
        count = 0
        for pk in items.values_list('pk', flat=True):
            if time.monotonic() - started >= 40:
                break
            count += bool(deliver(pk))
        self.stdout.write(f'{count} messages accepted by the configured mail backend.')
