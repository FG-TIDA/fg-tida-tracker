from django.db import migrations
from django.db.models import Q


def remove_from_roster(apps, schema_editor):
    # Retain any linked files, roles, submissions and repository references.
    # This is an administrative record, not a published working group or a
    # formal WG-conclusion decision. The meeting agenda text is independent.
    Group = apps.get_model('group', 'Group')
    Group.objects.using(schema_editor.connection.alias).filter(parent__acronym='tida').filter(
        Q(acronym='tidawg5') | Q(tida_configuration__number=5)).update(type_id='team')


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0046_update_working_group_names')]
    operations = [migrations.RunPython(remove_from_roster, migrations.RunPython.noop)]
