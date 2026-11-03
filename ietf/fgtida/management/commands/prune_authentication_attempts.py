from django.core.management.base import BaseCommand
from django.utils import timezone
from ietf.fgtida.models import AuthenticationAttempt


class Command(BaseCommand):
    help = 'Remove expired authentication throttling records; schedule daily in production.'

    def handle(self, *args, **options):
        count, _ = AuthenticationAttempt.objects.filter(expires__lt=timezone.now()).delete()
        self.stdout.write(f'Removed {count} expired authentication records.')
