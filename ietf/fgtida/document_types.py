"""Public document types; legacy source labels are only import hints."""
NAMES = {'themes': 'Theme', 'use-cases': 'Use case', 'templates': 'Template',
         'deliverables': 'Deliverable', 'resources': 'Resource', 'contributions': 'Contribution',
         'slides': 'Slide', 'minutes': 'Meeting minutes'}


def singular(value):
    return NAMES.get(value.lower(), value)


def search_type_choices():
    """Use the same current, deduplicated type options in every search form."""
    from . import core
    from .models import PublicDocumentType
    public_types = list(PublicDocumentType.objects.all())
    public_names = {record.name for record in public_types}
    choices = {key: singular(label) for key, label in core.TYPE_LABELS if singular(label) not in public_names}
    choices.update({f'public-{record.pk}': record.name for record in public_types})
    return choices


def source_type(doc, category):
    if category == 'use-cases' and doc.group.acronym == 'tidawg0':
        return 'Contribution'
    return NAMES.get(category, singular(category.replace('-', ' ').capitalize())) if category else ''


def assign_source_type(doc, category):
    from .models import DocumentAttribution, PublicDocumentType
    name = source_type(doc, category)
    if not name:
        return
    metadata, _ = DocumentAttribution.objects.get_or_create(document=doc)
    if not metadata.public_type_id:
        metadata.public_type = PublicDocumentType.objects.get_or_create(name=name)[0]
        metadata.save(update_fields=['public_type'])
    if category == 'use-cases' and doc.group.acronym == 'tidawg0':
        metadata.additional_types.add(PublicDocumentType.objects.get_or_create(name='Use case')[0])
        if metadata.public_type.name != 'Contribution':
            metadata.additional_types.add(PublicDocumentType.objects.get_or_create(name='Contribution')[0])
        number_use_case(doc)


def number_use_case(doc):
    """Share the normal submission sequence without creating a second document."""
    from django.db import transaction
    from ietf.doc.models import Document
    from .models import Contribution, ContributionSequence
    from .meeting_submissions import unique_open_meeting
    with transaction.atomic():
        Document.objects.select_for_update().get(pk=doc.pk)
        if Contribution.objects.filter(document=doc).exists():
            return
        prefix = 'FG-TIDA-WG0'
        sequence = ContributionSequence.lock(prefix)
        maximum = Contribution.objects.filter(number_prefix=prefix).order_by('-sequence_number').values_list('sequence_number', flat=True).first() or 0
        if sequence.last_number != maximum:
            sequence.last_number = maximum
            sequence.save(update_fields=['last_number'])
        Contribution.objects.create(document=doc, group=doc.group, title=doc.title,
            abstract=doc.abstract, submission_method='github', draft_repository_url=doc.external_url,
            filename='', content=b'', status='catalogued', notifications_queued=True,
            meeting=unique_open_meeting())
