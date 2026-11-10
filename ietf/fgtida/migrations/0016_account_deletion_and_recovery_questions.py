import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def add_questions(apps, schema_editor):
    question = apps.get_model('fgtida', 'SecurityQuestion')
    prompts = [
        ('private-codeword', 'What codeword did you choose just for this account?'),
        ('invented-title', 'What title did you invent for an imaginary book?'),
        ('imaginary-map', 'What name did you invent for a place on an imaginary map?'),
        ('word-pair', 'What pair of unrelated words did you choose for recovery?'),
        ('imaginary-team', 'What name did you invent for an imaginary team?'),
        ('private-mnemonic', 'What private mnemonic did you create for account recovery?'),
    ]
    for order, (slug, prompt) in enumerate(prompts, start=10):
        question.objects.using(schema_editor.connection.alias).get_or_create(
            slug=slug, defaults={'prompt': prompt, 'order': order})


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0015_githubrepository_category_evidence_and_more')]

    operations = [
        migrations.AlterField(
            model_name='contribution', name='submitter',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='administratorapproval', name='approved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='administrator_approvals', to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(add_questions, migrations.RunPython.noop),
    ]
