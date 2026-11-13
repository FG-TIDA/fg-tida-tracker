"""Rename the template metadata while retaining source, content and archives."""
from django.db import migrations


def rename(apps, schema_editor):
    alias = schema_editor.connection.alias
    sources = apps.get_model('fgtida', 'GitHubSource').objects.using(alias).filter(
        repository__account__organization__iexact='FG-TIDA', repository__name='spec-template')
    apps.get_model('doc', 'Document').objects.using(alias).filter(
        pk__in=sources.values('document_id'), type_id='tida-template').update(title='New Work Item Template (NWI)')


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0021_retire_legacy_wg1_repository')]
    operations = [migrations.RunPython(rename, migrations.RunPython.noop)]
