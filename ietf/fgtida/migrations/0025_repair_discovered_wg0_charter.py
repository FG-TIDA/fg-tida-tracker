"""Correct the discovery default only for WG0's verified Markdown charter."""
from django.db import migrations


def repair(apps, schema_editor):
    repositories = apps.get_model('fgtida', 'GitHubRepository').objects.using(schema_editor.connection.alias)
    for repository in repositories.filter(account__organization__iexact='fg-tida-wg0', name='wg0-uct-charter',
            github_id=1451540158, mode='workitem', paths=['src/doc/index.rst']):
        repository.mode = 'files'
        repository.paths = ['charter.md']
        repository.document_type = 'charter'
        repository.save(using=schema_editor.connection.alias, update_fields=['mode', 'paths', 'document_type'])


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0024_wg0_charter_source')]
    operations = [migrations.RunPython(repair, migrations.RunPython.noop)]
