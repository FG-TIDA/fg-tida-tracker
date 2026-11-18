"""Private quarantine, fail-closed ClamAV streaming, and protected original files."""
import hashlib
import ipaddress
import logging
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
from contextlib import contextmanager

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import FileScan

log = logging.getLogger(__name__)
MAX_SCAN_BYTES = 100 * 1024 * 1024


def required():
    return getattr(settings, 'FGTIDA_UPLOAD_SCAN_REQUIRED', False)


def format_key(filename):
    if filename == 'github-repository.zip':
        return 'repository'
    return 'tar.gz' if filename.endswith('.tar.gz') else Path(filename).suffix.lower().lstrip('.')


def quarantine_path(digest):
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Invalid file checksum.')
    root = Path(settings.FGTIDA_QUARANTINE_DIR)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise OSError('Quarantine must be a private directory.')
    for setting in ('STATIC_ROOT', 'MEDIA_ROOT'):
        exposed = getattr(settings, setting, '')
        if exposed and root.resolve().is_relative_to(Path(exposed).resolve()):
            raise OSError('Quarantine must be outside served file directories.')
    return root / digest


@contextmanager
def quarantine_lock(root):
    import fcntl
    with (root / '.lock').open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def store_private(data, digest):
    path = quarantine_path(digest)
    with quarantine_lock(path.parent):
        if path.exists():
            return path
        total = 0
        for item in path.parent.iterdir():
            try:
                if item.is_file():
                    total += item.stat().st_size
            except FileNotFoundError:
                continue
        if total + len(data) > 250 * 1024 * 1024:
            raise ValidationError('The upload security queue is full. Please try again later.')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(data)
    return path


def reply(connection):
    answer = bytearray()
    while len(answer) < 4096:
        part = connection.recv(1)
        if not part or part == b'\0':
            return answer.decode('utf-8', errors='replace')
        answer.extend(part)
    raise OSError('Invalid scanner response.')


def scanner_connection():
    host = getattr(settings, 'FGTIDA_CLAMAV_HOST', '')
    if not host:
        raise OSError('Private scanner is not configured.')
    for _, _, _, _, endpoint in socket.getaddrinfo(host, settings.FGTIDA_CLAMAV_PORT, type=socket.SOCK_STREAM):
        if ipaddress.ip_address(endpoint[0]).is_private:
            try:
                return socket.create_connection((endpoint[0], endpoint[1]), timeout=5)
            except OSError:
                continue
    raise OSError('The scanner must use a private network address.')


def scanner_version():
    with scanner_connection() as connection:
        connection.settimeout(10)
        connection.sendall(b'zVERSION\0')
        version = reply(connection)
    if not version.startswith('ClamAV ') or len(version.split('/')) < 3:
        raise OSError('Scanner definitions are unavailable.')
    # VERSION reports the signature database build time in a fixed English format.
    from email.utils import parsedate_to_datetime
    try:
        stamp = parsedate_to_datetime(version.split('/', 2)[2] + ' +0000')
        age = (timezone.now() - stamp).total_seconds()
    except (TypeError, ValueError, IndexError) as error:
        raise OSError('Scanner definition date is unavailable.') from error
    if age > 7 * 86400 or age < -86400:
        raise OSError('Scanner definitions need updating.')
    return version[:255]


def scan_stream(data):
    version = scanner_version()
    deadline = time.monotonic() + 45
    with scanner_connection() as connection:
        connection.settimeout(10)
        connection.sendall(b'zINSTREAM\0')
        for offset in range(0, len(data), 65536):
            if time.monotonic() > deadline:
                raise OSError('Scan exceeded its time limit.')
            chunk = data[offset:offset + 65536]
            connection.sendall(struct.pack('!I', len(chunk)) + chunk)
        connection.sendall(b'\0\0\0\0')
        result = reply(connection)
    if result == 'stream: OK':
        return version, ''
    if result.startswith('stream: ') and result.endswith(' FOUND'):
        return version, result[8:-6][:200]
    raise OSError('The scanner could not fully inspect the file.')


def validate_file(data, filename, *, force=False):
    if not required():
        return
    if not data or len(data) > MAX_SCAN_BYTES:
        raise ValidationError('Files must be between 1 byte and 100 MB for security checks.')
    digest = hashlib.sha256(data).hexdigest()
    record, _ = FileScan.objects.get_or_create(sha256=digest, defaults={
        'filename': Path(filename).name[:255], 'size': len(data)})
    kind = format_key(filename)
    if record.status == 'clean' and kind in record.formats and kind not in record.rejected_formats and not force:
        return
    if (record.status == 'rejected' or kind in record.rejected_formats) and not force:
        raise ValidationError('This file was rejected by the security checks.')
    try:
        path = store_private(data, digest)
    except OSError as error:
        raise ValidationError('The private file-check queue is temporarily unavailable. Please try again later.') from error
    with transaction.atomic():
        record = FileScan.objects.select_for_update().get(pk=digest)
        if record.status == 'clean' and kind in record.formats and kind not in record.rejected_formats and not force:
            return
        structure_error = None
        try:
            # Scan raw bytes on the private service before running document parsers.
            engine, detection = (record.engine, '') if record.status == 'clean' and not force else scan_stream(data)
        except OSError:
            record.status = 'error'
            record.detail = 'Scanner unavailable or definitions outdated. File remains private.'
        else:
            record.engine = engine
            record.detail = detection
            if detection:
                record.status = 'rejected'
                record.formats = []
            else:
                try:
                    inspect_file(data, kind)
                except ValidationError as error:
                    structure_error = error
                    record.rejected_formats = sorted(set(record.rejected_formats) | {kind})
                    record.formats = [item for item in record.formats if item != kind]
                    record.status = 'clean' if record.formats else 'blocked'
                    record.detail = f'Unsafe document structure for {kind}.'
                else:
                    record.status = 'clean'
                    record.formats = sorted(set(record.formats) | {kind})
                    record.rejected_formats = [item for item in record.rejected_formats if item != kind]
            path.unlink(missing_ok=True)
        record.checked = timezone.now()
        record.attempts += 1
        record.save()
    log.info('File security check sha256=%s status=%s', digest, record.status)
    if structure_error:
        raise structure_error
    if record.status != 'clean':
        raise ValidationError('This file was rejected by the security checks.' if record.status == 'rejected' else
                              'Your file is held privately while scanning is unavailable. Please retry your submission after scanning is restored.')


def inspect_file(data, kind):
    from .parser_process import environment
    try:
        subprocess.run([sys.executable, '-m', 'ietf.fgtida.safety_worker', kind], input=data,
                       capture_output=True, timeout=20, check=True, env=environment())
    except (OSError, subprocess.SubprocessError) as error:
        raise ValidationError('This file contains unsupported, active, encrypted or unsafe content. Export a plain document and try again.') from error


def allowed(data, filename):
    if not required():
        return True
    return allowed_digest(hashlib.sha256(data).hexdigest(), filename)


def allowed_digest(digest, filename):
    if not required():
        return True
    record = FileScan.objects.filter(sha256=digest, status='clean').first() if digest else None
    return bool(record and format_key(filename) in record.formats and format_key(filename) not in record.rejected_formats)


def snapshot_allowed(snapshot):
    if not required():
        return True
    document = snapshot.doc if snapshot.is_dochistory() else snapshot
    version = next((row for row in document.tida_versions.all() if row.revision == snapshot.rev), None)
    digest = version.source_metadata.get('file_sha256', '') if version else ''
    return allowed_digest(digest, snapshot.uploaded_filename or '')


def require_clean(data, filename):
    if not allowed(data, filename):
        raise PermissionDenied('This file is awaiting security checks and is temporarily unavailable.')


def require_clean_digest(digest, filename):
    if not allowed_digest(digest, filename):
        raise PermissionDenied('This file is awaiting security checks and is temporarily unavailable.')


def safe_document_text(snapshot):
    if not snapshot.uploaded_filename:
        return ''
    path = Path(snapshot.get_file_name())
    if not path.is_file():
        return ''
    if not required():
        return snapshot.text() or ''
    data = path.read_bytes()
    if not allowed(data, snapshot.uploaded_filename):
        return None
    # Rebuild the preview from the scanned original, never trust an old extracted sidecar.
    from .files import extract_text
    from django.core.cache import cache
    key = 'fgtida:checked-preview:1:' + hashlib.sha256(data).hexdigest() + ':' + format_key(snapshot.uploaded_filename)
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        text = extract_text(data, snapshot.uploaded_filename)
    except ValidationError:
        return None
    if len(text.encode('utf-8')) < 500000:
        cache.set(key, text, 3600)
    return text
