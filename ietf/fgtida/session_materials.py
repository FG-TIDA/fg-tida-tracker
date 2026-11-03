"""Public session downloads, with bounded-memory TAR streaming."""
import tarfile
from pathlib import Path
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_safe
from ietf.meeting.models import Session
from . import core


def file_chunks(path):
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            yield chunk


def attachment_chunks(resource):
    for content in resource.file_chunks.order_by('position').values_list('content', flat=True).iterator(chunk_size=1):
        yield bytes(content)


@require_safe
def archive(request, pk):
    session = get_object_or_404(Session, pk=pk, meeting__type_id='tida', group__in=core.groups())
    files, links = [], []
    for doc in core.documents().filter(session=session).distinct():
        if doc.uploaded_filename:
            path = Path(doc.get_file_name())
            if path.is_file():
                files.append((f'documents/{doc.pk}-{path.name}', path.stat().st_size, lambda p=path: file_chunks(p)))
        if doc.external_url:
            links.append(f'{doc.title}: {doc.external_url}')
    for resource in session.tida_resources.all():
        if resource.file_size:
            files.append((f'resources/{resource.pk}-{Path(resource.filename).name}', resource.file_size,
                lambda r=resource: attachment_chunks(r)))
        elif resource.url:
            links.append(f'{resource.title}: {resource.url}')
    manifest = ('Session: ' + (session.name or str(session.group)) + '\n'
        + 'External resources are listed below; their remote files are not fetched.\n'
        + '\n'.join(links) + '\n').encode('utf-8')
    files.append(('README.txt', len(manifest), lambda: iter([manifest])))

    def stream():
        for name, size, chunks in files:
            info = tarfile.TarInfo(name)
            info.size = size
            info.mode = 0o644
            yield info.tobuf(format=tarfile.PAX_FORMAT)
            remaining = size
            for part in chunks():
                if len(part) > remaining:
                    raise IOError('Material changed during download; retry the archive.')
                remaining -= len(part)
                yield part
            if remaining:
                raise IOError('Material changed during download; retry the archive.')
            yield b'\0' * (-size % 512)
        yield b'\0' * 1024
    response = StreamingHttpResponse([] if request.method == 'HEAD' else stream(), content_type='application/x-tar')
    response['Content-Disposition'] = f'attachment; filename="fg-tida-session-{session.pk}-materials.tar"'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'no-store'
    return response
