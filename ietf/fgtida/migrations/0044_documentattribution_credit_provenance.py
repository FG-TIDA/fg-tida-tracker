from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0043_paris_submission_meeting')]
    operations = [
        migrations.AddField(model_name='documentattribution', name='credit_provenance',
            field=models.JSONField(default=dict, editable=False,
                help_text='Tracks automatic values and deliberate editor overrides independently.')),
        migrations.AlterField(model_name='documentattribution', name='authors',
            field=models.TextField('Author(s)', blank=True,
                help_text='One author per line. Your edits override imported metadata, including an explicitly cleared value.')),
    ]
