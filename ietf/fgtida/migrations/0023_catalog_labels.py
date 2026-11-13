from django.db import migrations


def rename(apps, schema_editor):
    alias = schema_editor.connection.alias
    sources = apps.get_model('fgtida', 'GitHubSource').objects.using(alias).filter(
        repository__account__organization__iexact='FG-TIDA', repository__name='wg-charter-template')
    apps.get_model('doc', 'Document').objects.using(alias).filter(
        pk__in=sources.values('document_id'), type_id='tida-template').update(title='Charter Template')
    groups = apps.get_model('group', 'Group').objects.using(alias).filter(parent__acronym='tida', description__contains='Public source:')
    for group in groups:
        group.description = group.description.replace('Public source:', 'GitHub source:')
        group.save(using=alias, update_fields=['description'])


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0022_rename_nwi_template')]
    operations = [migrations.RunPython(rename, migrations.RunPython.noop)]
