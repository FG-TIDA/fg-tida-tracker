"""A single checked catalogue for session buttons and the materials dialog."""
from pathlib import Path
from urllib.parse import urlsplit
from django.urls import reverse
from . import core


def inventory(session):
    if hasattr(session, '_materials_inventory'):
        return session._materials_inventory
    from .file_safety import allowed_digest, format_key, required
    from .models import FileScan
    docs = getattr(session, 'public_materials', None)
    if docs is None:
        docs = core.documents().filter(session=session).prefetch_related('tida_versions').distinct()
    docs = list(docs)
    resources = list(session.tida_resources.all())
    digests = {}
    for doc in docs:
        current = next((row for row in doc.tida_versions.all() if row.revision == doc.rev), None)
        digests[doc.pk] = current.source_metadata.get('file_sha256', '') if current else ''
    hashes = set(digests.values()) | {row.sha256 for row in resources if row.file_size}
    hashes.update(row.sha256 for doc in docs for row in doc.tida_formats.all() if row.revision == doc.rev and row.is_public)
    scans = {row.sha256: row for row in FileScan.objects.filter(sha256__in=hashes)} if required() else {}

    def checked(digest, filename):
        if not required():
            return True
        row, kind = scans.get(digest), format_key(filename)
        if row and row.status in {'clean', 'admin'} and kind in row.formats and kind not in row.rejected_formats:
            return True
        return allowed_digest(digest, filename) if row else False

    grouped = {'agenda': [], 'slides': [], 'minutes': [], 'recording': []}
    ready = True
    for doc in docs:
        available = not doc.uploaded_filename or (checked(digests[doc.pk], doc.uploaded_filename) and Path(doc.get_file_name()).is_file())
        ready = ready and available
        key = 'agenda' if doc.type_id == 'agenda' else 'minutes' if doc.type_id in {'minutes', 'narrativeminutes'} else 'recording' if doc.type_id == 'recording' else 'slides'
        grouped[key].append({'title': doc.title, 'url': reverse('fgtida:document', args=[doc.name]),
            'available': available, 'format': Path(doc.uploaded_filename or '').suffix.lower()})
        if key == 'slides':
            for alternative in doc.tida_formats.all():
                if alternative.revision == doc.rev and alternative.is_public and alternative.kind in {'pdf', 'pptx'}:
                    grouped[key].append({'title': f'{doc.title} ({alternative.kind.upper()})',
                        'url': reverse('fgtida:document-format', args=[alternative.pk]),
                        'available': checked(alternative.sha256, alternative.filename), 'format': '.' + alternative.kind})
    for resource in resources:
        if resource.kind == 'login':
            continue
        available = not resource.file_size or checked(resource.sha256, resource.filename)
        ready = ready and available
        key = 'agenda' if resource.kind == 'agenda' else 'minutes' if resource.kind in {'minutes', 'notes'} else 'recording' if resource.kind == 'recording' else 'slides'
        grouped[key].append({'title': resource.title, 'url': resource.resource_url, 'available': available,
            'format': Path(resource.filename or urlsplit(resource.url).path).suffix.lower()})
    session._materials_inventory = grouped, ready
    return grouped, ready
