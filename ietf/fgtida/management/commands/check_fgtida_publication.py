"""Release gate: do not mistake a working development demo for production."""
from pathlib import Path
import django
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Q
from django.db.migrations.executor import MigrationExecutor
from ietf.doc.models import Document
from ietf.meeting.models import Meeting
from ietf.fgtida import core


class Command(BaseCommand):
    help = 'Fail unless production security, data scope, domain and file checks pass.'

    def add_arguments(self, parser):
        parser.add_argument('--allow-deferred-smtp', action='store_true',
            help='Check a private preparation environment with email disabled; not final launch approval.')

    def handle(self, *args, **options):
        failures = []
        if settings.DEBUG or settings.SERVER_MODE != 'production':
            failures.append('Development mode is enabled.')
        if '*' in settings.ALLOWED_HOSTS:
            failures.append('Wildcard hostnames are enabled.')
        if not all([settings.SECURE_SSL_REDIRECT, settings.SESSION_COOKIE_SECURE, settings.CSRF_COOKIE_SECURE]):
            failures.append('HTTPS redirect and secure cookies must be enabled.')
        if django.VERSION < (5, 2, 18):
            failures.append('Django must be at least the patched 5.2.18 release.')
        if getattr(settings, 'USING_DEBUG_EMAIL_SERVER', False):
            failures.append('Debug email is enabled.')
        if not getattr(settings, 'FGTIDA_MAIL_ENABLED', False) and not options['allow_deferred_smtp']:
            failures.append('SMTP delivery is disabled; enable it and verify real reset-email delivery before launch.')
        if not getattr(settings, 'FGTIDA_REQUIRE_ADMIN_MFA', False):
            failures.append('Administrator MFA must be required.')
        if getattr(settings, 'LOGIN_REDIRECT_URL', '') != '/':
            failures.append('The default sign-in destination must be the home page.')
        domain = getattr(settings, 'FGTIDA_PUBLIC_DOMAIN', '')
        if not domain or Site.objects.get_current().domain != domain:
            failures.append('The active Site must match the configured FG-TIDA public domain.')
        if Document.objects.exclude(group__in=core.groups()).exists():
            failures.append('Non-FG-TIDA documents remain in the release database.')
        if Meeting.objects.exclude(type_id='tida').exists():
            failures.append('Non-FG-TIDA meetings remain in the release database.')
        approved = getattr(settings, 'FGTIDA_APPROVED_PRIVILEGED_USERS', [])
        from ietf.fgtida.models import AdministratorApproval
        approved = list(approved) + list(AdministratorApproval.objects.values_list('user__username', flat=True))
        privileged = get_user_model().objects.filter(is_active=True).filter(
            Q(is_staff=True) | Q(is_superuser=True))
        if not approved or privileged.exclude(username__in=approved).exists():
            failures.append('Privileged accounts have not been explicitly approved for production.')
        from django_otp.plugins.otp_totp.models import TOTPDevice
        if privileged.exclude(pk__in=TOTPDevice.objects.filter(confirmed=True).values('user_id')).exists():
            failures.append('Every active administrator must enroll an authenticator before public launch.')
        from ietf.fgtida.models import SecurityQuestion
        if SecurityQuestion.objects.filter(active=True).count() < 3:
            failures.append('At least three security questions must be configured.')
        with connection.cursor() as cursor:
            cursor.execute('SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = current_user')
            role = cursor.fetchone()
        if role and any(role):
            failures.append('The application database role must not be superuser, CREATEROLE or CREATEDB.')
        from ietf.meeting.models import FloorPlan
        if any(not plan.image or not plan.image.storage.exists(plan.image.name) for plan in FloorPlan.objects.filter(meeting__type_id='tida')):
            failures.append('A meeting floor-plan file is missing.')
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            failures.append('Database migrations are pending.')
        missing = sum(not Path(doc.get_file_name()).is_file() for doc in core.documents().exclude(uploaded_filename=''))
        if missing:
            failures.append(f'{missing} referenced uploads are missing.')
        if failures:
            raise CommandError('\n'.join(failures))
        if options['allow_deferred_smtp']:
            self.stdout.write(self.style.WARNING('Preparation checks passed with SMTP deferred. Email delivery and final publication checks remain required.'))
        else:
            self.stdout.write(self.style.SUCCESS('Automated publication checks passed. Verify mail delivery, restore and hosting checks before release.'))
