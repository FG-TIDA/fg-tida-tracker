from django.db import migrations

QUESTIONS = [
    ('book', 'What was the title of a memorable childhood book?'),
    ('place', 'What place is associated with a personal memory you have not shared publicly?'),
    ('teacher', 'What nickname did you privately give a memorable teacher?'),
    ('project', 'What was the name of your first personal project?'),
    ('phrase', 'What private phrase will you remember for account recovery?'),
    ('object', 'What was a meaningful object from your childhood?'),
]


def seed(apps, schema_editor):
    Question = apps.get_model('fgtida', 'SecurityQuestion')
    Recovery = apps.get_model('fgtida', 'RecoveryQuestions')
    for order, (slug, prompt) in enumerate(QUESTIONS):
        Question.objects.get_or_create(slug=slug, defaults={'prompt': prompt, 'order': order})
    for recovery in Recovery.objects.all().iterator():
        recovery.prompts = {key: dict(QUESTIONS).get(key, 'Security answer') for key in recovery.questions}
        recovery.save(update_fields=['prompts'])


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0005_authenticationattempt_securityquestion_and_more')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
