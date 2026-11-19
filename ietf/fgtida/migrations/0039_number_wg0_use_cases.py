from django.db import migrations
from django.db.models import Q, Max


def number_use_cases(apps, schema_editor):
    alias = schema_editor.connection.alias
    Document = apps.get_model('doc', 'Document')
    Attribution = apps.get_model('fgtida', 'DocumentAttribution')
    Type = apps.get_model('fgtida', 'PublicDocumentType')
    Contribution = apps.get_model('fgtida', 'Contribution')
    Sequence = apps.get_model('fgtida', 'ContributionSequence')
    Source = apps.get_model('fgtida', 'GitHubSource')
    contribution_type, _ = Type.objects.using(alias).get_or_create(name='Contribution')
    use_case_type, _ = Type.objects.using(alias).get_or_create(name='Use case')
    sources = Source.objects.using(alias).filter(repository__name='use-cases',
        repository__account__organization__iexact='fg-tida-wg0').values('document_id')
    docs = Document.objects.using(alias).filter(group__acronym='tidawg0').filter(
        Q(tags__slug='tida-use-cases') | Q(tida_attribution__public_type=use_case_type) |
        Q(tida_attribution__additional_types=use_case_type) | Q(pk__in=sources)).distinct().order_by('time', 'pk')
    prefix = 'FG-TIDA-WG0'
    sequence, _ = Sequence.objects.using(alias).select_for_update().get_or_create(prefix=prefix)
    sequence.last_number = Contribution.objects.using(alias).filter(number_prefix=prefix).aggregate(n=Max('sequence_number'))['n'] or 0
    for doc in docs.iterator(chunk_size=200):
        metadata, _ = Attribution.objects.using(alias).get_or_create(document=doc)
        if not metadata.public_type_id:
            metadata.public_type = contribution_type
            metadata.save(using=alias, update_fields=['public_type'])
        # Preserve deliberate primary types and still apply both required labels.
        metadata.additional_types.add(use_case_type)
        if metadata.public_type_id != contribution_type.pk:
            metadata.additional_types.add(contribution_type)
        if Contribution.objects.using(alias).filter(document=doc).exists():
            continue
        sequence.last_number += 1
        Contribution.objects.using(alias).create(document=doc, group_id=doc.group_id,
            title=doc.title, abstract=doc.abstract, submission_method='github',
            draft_repository_url=doc.external_url, filename='', content=b'', status='catalogued',
            notifications_queued=True, number_prefix=prefix, sequence_number=sequence.last_number)
    sequence.save(using=alias, update_fields=['last_number'])


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0038_documentattribution_additional_types_and_more')]
    operations = [migrations.RunPython(number_use_cases, migrations.RunPython.noop)]
