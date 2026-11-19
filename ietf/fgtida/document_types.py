"""Public document types; legacy source labels are only import hints."""
NAMES = {'themes': 'Theme', 'use-cases': 'Use case', 'templates': 'Template',
         'deliverables': 'Deliverable', 'resources': 'Resource', 'contributions': 'Contribution',
         'slides': 'Slide', 'minutes': 'Meeting minutes'}


def singular(value):
    return NAMES.get(value.lower(), value)


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
