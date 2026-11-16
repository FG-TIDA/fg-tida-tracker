from django.db import migrations


def rename_legacy_incubation_label(apps, schema_editor):
    group = apps.get_model('group', 'Group')
    group.objects.using(schema_editor.connection.alias).filter(
        parent__acronym='tida', acronym='incubation', name='No working group yet',
    ).update(name='NWI for Incubation')


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0027_contribution_numbers')]
    operations = [migrations.RunPython(rename_legacy_incubation_label, migrations.RunPython.noop)]
