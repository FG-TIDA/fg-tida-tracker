from django.db import migrations


def add_rapporteur(apps, schema_editor):
    apps.get_model('name', 'RoleName').objects.using(schema_editor.connection.alias).get_or_create(
        slug='rapporteur', defaults={'name': 'Rapporteur'})


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0044_documentattribution_credit_provenance')]
    operations = [migrations.RunPython(add_rapporteur, migrations.RunPython.noop)]
