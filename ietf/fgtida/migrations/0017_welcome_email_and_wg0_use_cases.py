from django.db import migrations, models


def move_use_cases(apps, schema_editor):
    alias = schema_editor.connection.alias
    repositories = apps.get_model('fgtida', 'GitHubRepository').objects.using(alias)
    old = repositories.filter(account__organization__iexact='FG-TIDA', name='use-cases').first()
    if old is None:
        return
    # Verified against both GitHub API URLs. Update the row in place so all
    # source links, exclusions, queued jobs and archive revisions keep their IDs.
    github_id = 1354561082
    if old.github_id not in (None, github_id):
        raise RuntimeError('The old use-cases mapping has an unexpected GitHub ID. Review it before moving it to wg0.')
    if repositories.exclude(pk=old.pk).filter(
        models.Q(account__organization__iexact='fg-tida-wg0', name='use-cases') | models.Q(github_id=github_id)
    ).exists():
        raise RuntimeError('A second use-cases mapping already exists. Review its linked sources before merging the mappings.')
    account, _ = apps.get_model('fgtida', 'GitHubAccount').objects.using(alias).get_or_create(organization='fg-tida-wg0')
    group = apps.get_model('group', 'Group').objects.using(alias).get(acronym='tidawg0')
    url = 'https://github.com/fg-tida-wg0/use-cases'
    old.account_id = account.pk
    old.group_id = group.pk
    old.github_id = github_id
    old.group_evidence = url
    old.category = 'use-cases'
    old.category_evidence = url + '#readme'
    old.save(update_fields=['account', 'group', 'github_id', 'group_evidence', 'category', 'category_evidence'])


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0016_account_deletion_and_recovery_questions')]

    operations = [
        # Render runs migrations before replacing the previous web process.
        migrations.AddField(model_name='outgoingmail', name='html_body',
                            field=models.TextField(blank=True, db_default='')),
        migrations.RunPython(move_use_cases, migrations.RunPython.noop),
    ]
