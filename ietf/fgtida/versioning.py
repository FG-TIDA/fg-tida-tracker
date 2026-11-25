"""Stable public versions; existing archive paths and URLs remain valid."""
import re
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import DocumentVersion, DocumentVersionAlias
from .source_versions import declared_version


def parts(value):
    if not re.fullmatch(r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})',value or ''):
        raise ValidationError('Use X.Y notation, for example 0.0, 0.1 or 1.0.')
    return tuple(map(int,value.split('.')))


def next_version(document, requested=None):
    versions=list(DocumentVersion.objects.filter(document=document).values_list('version',flat=True)) if document.pk else []
    latest=max((parts(value) for value in versions),default=None)
    if requested is not None:
        value=parts(requested)
        if document.pk and DocumentVersionAlias.objects.filter(document=document, version=requested).exists():
            raise ValidationError('This version is reserved by a link to an earlier archive. Choose a new version.')
        if latest is not None and value<=latest:
            raise ValidationError('A new version must be greater than every archived version. Archived versions cannot be replaced.')
        return requested
    return f'{latest[0]}.{latest[1]+1}' if latest else '0.0'


def validate_correction(record, version):
    parts(version)
    if record.version == version:
        return
    item = getattr(record.document, 'tida_work_item', None)
    if item and item.releases.filter(revision=record.revision).exists():
        raise ValidationError('This archive belongs to a GitHub release. Publish a new vX.Y tag to change its version.')
    if DocumentVersion.objects.filter(document=record.document, version=version).exclude(pk=record.pk).exists():
        raise ValidationError('This version belongs to another archived revision.')
    if DocumentVersionAlias.objects.filter(document=record.document, version=version).exclude(target=record).exists():
        raise ValidationError('This version is already a link to another archive.')
    for other in DocumentVersion.objects.filter(document=record.document).exclude(pk=record.pk):
        if (int(other.revision) < int(record.revision) and parts(other.version) >= parts(version)) or (
                int(other.revision) > int(record.revision) and parts(other.version) <= parts(version)):
            raise ValidationError('Keep version numbers increasing in archive order.')


@transaction.atomic
def correct_version(record, version, actor):
    from ietf.doc.models import Document, DocEvent
    document = Document.objects.select_for_update().get(pk=record.document_id)
    record = DocumentVersion.objects.select_for_update().get(pk=record.pk)
    validate_correction(record, version)
    if record.version == version:
        return
    previous = record.version
    DocumentVersionAlias.objects.get_or_create(document=document, version=previous, defaults={'target': record})
    record.version = version
    record.save(update_fields=['version'])
    DocEvent.objects.create(doc=document, by=actor, type='changed_document', rev=record.revision,
        desc=f'Corrected displayed version from {previous} to {version}.')


def labels(document):
    if not hasattr(document,'_version_labels'):
        identifier=document.doc_id if document.is_dochistory() else document.pk
        rows=document.tida_versions.all() if not document.is_dochistory() else DocumentVersion.objects.filter(document_id=identifier)
        document._version_labels={row.revision:row.version for row in rows}
    return document._version_labels


def public_version(document,revision=None):
    revision=document.rev if revision is None else revision
    return labels(document).get(revision,revision or '—')


def archive_revision(document,value):
    if '.' in (value or ''):
        mapped = next((revision for revision,version in labels(document).items() if version==value), None)
        if mapped:
            return mapped
        alias = DocumentVersionAlias.objects.filter(document=document, version=value).select_related('target').first()
        return alias.target.revision if alias else value
    return value
