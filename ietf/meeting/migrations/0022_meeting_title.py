from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('meeting', '0021_alter_meeting_time_zone')]
    operations = [migrations.AddField(
        model_name='meeting', name='title',
        field=models.CharField(max_length=255, blank=True,
            help_text='Full public meeting name. Spaces and punctuation are allowed.'))]
