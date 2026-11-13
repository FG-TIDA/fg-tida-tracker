"""Retire the superseded WG1 source without deleting its archived material."""
from django.db import migrations


def retire(apps, schema_editor):
    alias = schema_editor.connection.alias
    repositories = apps.get_model('fgtida', 'GitHubRepository').objects.using(alias).filter(
        account__organization__iexact='FG-TIDA', name='wg1-ra-aai')
    sources = apps.get_model('fgtida', 'GitHubSource').objects.using(alias).filter(repository__in=repositories)
    items = apps.get_model('fgtida', 'WorkItem').objects.using(alias).filter(repository__in=repositories)
    ids = set(sources.values_list('document_id', flat=True)) | set(items.values_list('document_id', flat=True))
    documents = apps.get_model('doc', 'Document').objects.using(alias).filter(pk__in=ids)
    tag, _ = apps.get_model('name', 'DocTagName').objects.using(alias).get_or_create(
        slug='tida-uncatalogued', defaults={'name': 'Not in public catalog'})
    for document in documents:
        document.tags.add(tag)
    sources.update(sync_mode='excluded')
    items.update(sync_mode='excluded')
    repositories.update(enabled=False)


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0020_assign_use_cases_to_wg0')]
    operations = [migrations.RunPython(retire, migrations.RunPython.noop)]
