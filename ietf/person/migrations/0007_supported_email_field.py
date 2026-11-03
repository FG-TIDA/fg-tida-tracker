from django.db import migrations
from django.core.validators import validate_email
from ietf.person.db_fields import CaseInsensitiveEmailField


class Migration(migrations.Migration):
    dependencies = [('person', '0006_personuuid')]
    # Both fields still use the existing citext column; no data/DDL rewrite.
    operations = [migrations.SeparateDatabaseAndState(state_operations=[
        migrations.AlterField(model_name='email', name='address',
            field=CaseInsensitiveEmailField(max_length=64, primary_key=True, serialize=False, validators=[validate_email])),
        migrations.AlterField(model_name='historicalemail', name='address',
            field=CaseInsensitiveEmailField(max_length=64, db_index=True, validators=[validate_email])),
    ])]
