from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0047_remove_wg5_from_working_groups')]
    operations = [migrations.AddField(
        model_name='documentformat', name='is_public',
        field=models.BooleanField(default=True, editable=False),
    )]
