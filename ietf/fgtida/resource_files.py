"""Bounded PostgreSQL attachments and seekable media responses."""
from pathlib import Path
import re
import uuid
from django import forms
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_safe
from . import core
from .models import SessionResource, SessionResourceChunk

CHUNK_SIZE = 1024 * 1024
MEDIA_LIMIT = 100 * CHUNK_SIZE
MEDIA = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4'}
DOCUMENTS = {'.pdf': 'application/pdf', '.txt': 'text/plain', '.md': 'text/plain',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}


class SessionResourceForm(forms.ModelForm):
    upload = forms.FileField(required=False, label='Attach file', help_text='Stored in PostgreSQL. PDF, DOCX, PPTX, TXT or Markdown: up to 10 MB. Recordings (MP4, WebM, MP3, WAV, M4A): up to 100 MB. Published session attachments are public.')

    class Meta:
        model = SessionResource
        fields = ['session', 'kind', 'title', 'upload', 'url']

    def clean(self):
        values = super().clean()
        upload, url, kind = values.get('upload'), values.get('url'), values.get('kind')
        if upload:
            if url:
                self.add_error('url', 'Use an attachment or a URL, not both. Clear the URL to upload a file.')
            suffix = Path(upload.name).suffix.lower()
            if kind == 'login':
                self.add_error('upload', 'Remote participation requires a meeting URL.')
            elif kind == 'recording':
                header = upload.read(16)
                upload.seek(0)
                valid = {'.mp4': header[4:8] == b'ftyp', '.m4a': header[4:8] == b'ftyp',
                    '.webm': header.startswith(b'\x1aE\xdf\xa3'),
                    '.mp3': header.startswith(b'ID3') or (len(header) >= 2 and header[0] == 255 and header[1] & 224 == 224),
                    '.wav': header[:4] == b'RIFF' and header[8:12] == b'WAVE'}
                if suffix not in MEDIA or not valid.get(suffix) or not 0 < upload.size <= MEDIA_LIMIT:
                    self.add_error('upload', 'Upload a supported recording with a matching file signature, between 1 byte and 100 MB.')
            elif suffix not in DOCUMENTS:
                self.add_error('upload', 'Upload PDF, DOCX, PPTX, TXT or Markdown.')
            else:
                from .files import extract_text, MAX_BYTES
                if not 0 < upload.size <= MAX_BYTES:
                    self.add_error('upload', 'Document attachments must be between 1 byte and 10 MB.')
                else:
                    try:
                        extract_text(upload.read(MAX_BYTES + 1), upload.name)
                    except forms.ValidationError as exc:
                        self.add_error('upload', exc)
                    finally:
                        upload.seek(0)
        elif not url and not self.instance.file_size:
            self.add_error('upload', 'Attach a file or enter a URL.')
        elif url and self.instance.file_size:
            self.add_error('url', 'This resource has an attachment. Create a separate resource for an external link.')
        elif self.instance.file_size and kind != self.instance.kind:
            self.add_error('kind', 'Upload a replacement file when changing its resource kind.')
        return values

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data.get('upload'):
            instance._pending_upload = self.cleaned_data['upload']
        if commit:
            instance.save()
            self.save_m2m()
        return instance


def store_upload(resource, upload):
    # Called within SessionResource.save's transaction; a failed replacement
    # leaves the previous attachment intact. Read one bounded chunk at a time.
    resource.file_chunks.all().delete()
    upload.seek(0)
    size = 0
    position = 0
    while data := upload.read(CHUNK_SIZE):
        size += len(data)
        if size > MEDIA_LIMIT:
            raise forms.ValidationError('Attachment exceeds 100 MB.')
        SessionResourceChunk.objects.create(resource=resource, position=position, content=data)
        position += 1
    if not size:
        raise forms.ValidationError('The attachment is empty.')
    resource.filename = Path(upload.name).name[:255]
    resource.file_size = size
    resource.content_type = (MEDIA | DOCUMENTS)[Path(upload.name).suffix.lower()]
    resource.file_version = uuid.uuid4().hex
    resource.url = ''
    SessionResource.objects.filter(pk=resource.pk).update(filename=resource.filename,
        file_size=size, content_type=resource.content_type, file_version=resource.file_version, url='')


@require_safe
def download(request, pk):
    resource = get_object_or_404(SessionResource, pk=pk, file_size__gt=0,
        session__meeting__type_id='tida', session__group__in=core.groups())
    size = resource.file_size
    etag = f'"{resource.file_version}"'
    start, end, status = 0, size - 1, 200
    requested = request.headers.get('Range', '')
    if request.headers.get('If-Range', etag) != etag:
        requested = ''
    if requested:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
        try:
            if not match or not any(match.groups()):
                raise ValueError()
            first, last = match.groups()
            if first:
                start, end = int(first), min(int(last), size - 1) if last else size - 1
            else:
                length = int(last)
                if length <= 0:
                    raise ValueError()
                start = max(0, size - length)
            if not 0 <= start <= end < size:
                raise ValueError()
        except ValueError:
            return HttpResponse(status=416, headers={'Content-Range': f'bytes */{size}'})
        status = 206

    def chunks():
        # One cursor/snapshot, fetched a chunk at a time. Concurrent replacement
        # cannot mix bytes from different versions in a response.
        records = SessionResourceChunk.objects.filter(resource_id=resource.pk,
            resource__file_version=resource.file_version,
            position__gte=start // CHUNK_SIZE, position__lte=end // CHUNK_SIZE)
        for position, part in records.values_list('position', 'content').iterator(chunk_size=1):
            low = max(0, start - position * CHUNK_SIZE)
            high = min(len(part), end - position * CHUNK_SIZE + 1)
            yield bytes(part)[low:high]

    response = StreamingHttpResponse([] if request.method == 'HEAD' else chunks(), status=status, content_type=resource.content_type)
    response['Content-Length'] = str(end - start + 1)
    response['Accept-Ranges'] = 'bytes'
    response['ETag'] = etag
    response['Cache-Control'] = 'no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Disposition'] = content_disposition_header(resource.kind != 'recording', resource.filename)
    if status == 206:
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
    return response
