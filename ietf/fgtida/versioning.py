"""Stable public versions; existing archive paths and URLs remain valid."""
import re
from django.core.exceptions import ValidationError
from .models import DocumentVersion


def parts(value):
    if not re.fullmatch(r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})',value or ''):
        raise ValidationError('Use X.Y notation, for example 0.0, 0.1 or 1.0.')
    return tuple(map(int,value.split('.')))


def next_version(document, requested=None):
    versions=list(DocumentVersion.objects.filter(document=document).values_list('version',flat=True)) if document.pk else []
    latest=max((parts(value) for value in versions),default=None)
    if requested is not None:
        value=parts(requested)
        if latest is not None and value<=latest:
            raise ValidationError('A new version must be greater than every archived version. Archived versions cannot be replaced.')
        return requested
    return f'{latest[0]}.{latest[1]+1}' if latest else '0.0'


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
        return next((revision for revision,version in labels(document).items() if version==value),value)
    return value
