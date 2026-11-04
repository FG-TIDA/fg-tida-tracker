"""Repair fresh Django 5 databases while preserving legacy citext identities.

Historical CICharField migrations become varchar under newer Django releases.
0007 changed migration state only, which was sufficient for existing citext
databases but left fresh installations with case-sensitive email columns.
Use Django's schema editor to update the primary key, referencing foreign keys,
history column and their indexes together in an atomic migration.
"""
from django.contrib.postgres.operations import CITextExtension
from django.core.validators import validate_email
from django.db import migrations, models

from ietf.person.db_fields import CaseInsensitiveEmailField


def check_email_collisions(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    table = schema_editor.quote_name(apps.get_model('person', 'Email')._meta.db_table)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'SELECT 1 FROM {table} GROUP BY lower(address::text) HAVING count(*) > 1 LIMIT 1')
        if cursor.fetchone():
            raise RuntimeError('Email addresses differing only by case already exist. Review and reconcile those identities before migrating; no accounts have been deleted.')


class Migration(migrations.Migration):
    dependencies = [('person', '0007_supported_email_field')]
    operations = [
        # Deliberately irreversible: rolling back must not weaken identity
        # uniqueness. This guard is evaluated before any schema changes.
        migrations.RunPython(check_email_collisions),
        CITextExtension(),
        # Model the actual type produced by historical migrations on Django 5,
        # then let normal AlterField operations maintain all referencing FKs.
        # On existing citext installations the cast preserves stored values.
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.AlterField(model_name='email', name='address',
                field=models.CharField(max_length=64, primary_key=True, serialize=False, validators=[validate_email])),
            migrations.AlterField(model_name='historicalemail', name='address',
                field=models.CharField(max_length=64, db_index=True, validators=[validate_email])),
        ]),
        migrations.AlterField(model_name='email', name='address',
            field=CaseInsensitiveEmailField(max_length=64, primary_key=True, serialize=False, validators=[validate_email])),
        migrations.AlterField(model_name='historicalemail', name='address',
            field=CaseInsensitiveEmailField(max_length=64, db_index=True, validators=[validate_email])),
    ]
