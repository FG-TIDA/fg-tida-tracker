from django.db import migrations


def configure(apps, schema_editor):
    alias = schema_editor.connection.alias
    group = apps.get_model('group', 'Group').objects.using(alias).filter(acronym='tidawg0', parent__acronym='tida').first()
    if not group:
        return
    account, _ = apps.get_model('fgtida', 'GitHubAccount').objects.using(alias).get_or_create(organization='fg-tida-wg0')
    apps.get_model('fgtida', 'GitHubRepository').objects.using(alias).get_or_create(
        account=account, name='wg0-uct-charter', defaults={
            'group': group, 'github_id': 1451540158, 'branch': 'main',
            'mode': 'files', 'document_type': 'charter', 'paths': ['charter.md'],
            'group_evidence': 'https://github.com/fg-tida-wg0/wg0-uct-charter'})


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0023_catalog_labels')]
    operations = [migrations.RunPython(configure, migrations.RunPython.noop)]
