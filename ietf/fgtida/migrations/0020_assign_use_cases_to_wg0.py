"""Apply the FG-TIDA decision that use cases belong to WG0; retain archives."""
from django.db import migrations, models
from django.utils import timezone


def move_use_cases(apps, schema_editor):
    alias = schema_editor.connection.alias
    Group = apps.get_model('group', 'Group')
    Group.objects.using(alias).filter(acronym='incubation', parent__acronym='tida').update(name='No working group yet')
    wg0 = Group.objects.using(alias).filter(acronym='tidawg0', parent__acronym='tida').first()
    if not wg0:
        return
    Repository = apps.get_model('fgtida', 'GitHubRepository')
    sources = apps.get_model('fgtida', 'GitHubSource').objects.using(alias)
    repositories = Repository.objects.using(alias).filter(category='use-cases')
    repository_ids = list(repositories.values_list('pk', flat=True))
    repositories.update(group_id=wg0.pk)
    Document = apps.get_model('doc', 'Document')
    documents = Document.objects.using(alias).filter(
        models.Q(tags__slug='tida-use-cases') | models.Q(pk__in=sources.filter(repository_id__in=repository_ids).values('document_id'))
    ).filter(models.Q(group__parent__acronym='tida') | models.Q(group__acronym='tida')).exclude(group_id=wg0.pk).distinct()
    actor, _ = apps.get_model('person', 'Person').objects.using(alias).get_or_create(
        name='FG-TIDA Tracker', ascii='FG-TIDA Tracker', user=None)
    Event = apps.get_model('doc', 'DocEvent')
    for doc in documents:
        doc.group_id = wg0.pk
        doc.save(using=alias, update_fields=['group'])
        Event.objects.using(alias).create(doc_id=doc.pk, by_id=actor.pk, type='changed_document',
            rev=doc.rev, time=timezone.now(), desc='Assigned use-case material to WG0. Existing files and revisions retained.')


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0019_alter_outgoingmail_html_body')]
    operations = [migrations.RunPython(move_use_cases, migrations.RunPython.noop)]
