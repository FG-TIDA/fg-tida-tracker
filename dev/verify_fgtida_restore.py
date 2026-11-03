"""Restore the private release fixture into a new isolated database; no source writes."""
import os
import copy
import re
import psycopg2
from psycopg2 import sql

os.environ['DJANGO_SETTINGS_MODULE'] = 'ietf.settings'
from django.conf import settings
database = copy.deepcopy(settings.DATABASES['default'])
name = os.environ.get('FGTIDA_VERIFY_DATABASE', 'fgtida_release_verification')
if not re.fullmatch(r'fgtida_release_verification(?:_[a-z0-9_]+)?', name) or name == database['NAME']:
    raise ValueError('Restore verification must use a separate, explicitly named verification database.')
connection = psycopg2.connect(dbname=database['NAME'], user=database['USER'], password=database.get('PASSWORD'),
                             host=database['HOST'], port=database.get('PORT', 5432))
try:
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1 FROM pg_database WHERE datname = %s', [name])
        if cursor.fetchone():
            print('Resuming the isolated verification database; no source database writes.')
        else:
            cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
finally:
    connection.close()
settings.DATABASES = {'default': {**database, 'NAME': name}}
settings.DATABASE_ROUTERS = []
settings.ENABLE_BLOBSTORAGE = False
settings.BLOBDB_DATABASE = None
settings.BLOBDB_REPLICATION = {'ENABLED': False}
settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
settings.CELERY_BROKER_URL = 'memory://'
import django
django.setup()
from django.core.management import call_command
call_command('migrate', interactive=False, verbosity=0, skip_checks=True)
call_command('loaddata', os.environ.get('FGTIDA_VERIFY_FIXTURE', '/workspace/dev/security-private/release.json'), verbosity=1, skip_checks=True)
call_command('configure_fgtida', verbosity=0, skip_checks=True)
from ietf.fgtida import core
from django.contrib.auth import get_user_model
from ietf.doc.models import Document
from ietf.meeting.models import Meeting
assert not Document.objects.exclude(group__in=core.groups()).exists()
assert not Meeting.objects.exclude(type_id='tida').exists()
fixture = __import__('json').loads(__import__('pathlib').Path(os.environ.get('FGTIDA_VERIFY_FIXTURE', '/workspace/dev/security-private/release.json')).read_text())
expected_accounts = {row['fields']['username'] for row in fixture if row['model'] == 'auth.user'}
assert set(get_user_model().objects.values_list('username', flat=True)) == expected_accounts
assert get_user_model().objects.filter(is_active=True, is_superuser=True).count() == 1
from django.test import Client
client = Client()
for path, expected in [('/', 200), ('/doc/', 200), ('/meeting/', 200),
                       ('/api/v1/doc/document/', 404), ('/accounts/login/', 200)]:
    assert client.get(path).status_code == expected, path
print('Restore verified:', core.documents().count(), 'public documents,', core.meetings().count(), 'meetings,', len(expected_accounts), 'accounts.')
