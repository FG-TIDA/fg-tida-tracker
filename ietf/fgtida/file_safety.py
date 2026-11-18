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

from .models import FileScan, FileScanEvent, FileScanException

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
        if not part:
            raise OSError('Incomplete scanner response.')
        if part == b'\0':
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
    if age > 3 * 86400 or age < -86400:
        raise OSError('Scanner definitions need updating.')
    return version[:255]


def scanner_test_pattern():
    # Standard harmless antivirus test data. Never saved or published.
    return (b'X5O!P%@AP[4' + bytes([92]) + b'PZX54(P^)7CC)7}'
            + b'$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*')


def _scan_stream(data):
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
        return ''
    if result.startswith('stream: ') and result.endswith(' FOUND'):
        return result[8:-6][:200]
    raise OSError('The scanner could not fully inspect the file.')


def scan_stream(data):
    version = scanner_version()
    # Prove detection and clean-file handling before trusting any new verdict.
    # This also catches accidentally disabled scanning or an always-rejecting engine.
    detection = _scan_stream(scanner_test_pattern())
    if 'eicar' not in detection.casefold() or _scan_stream(b'FG-TIDA clean scanner control.\n'):
        raise OSError('Scanner detection/control self-test failed.')
    return version, _scan_stream(data)


def record_event(record, operation, *, kind='', actor=None, detail=None):
    return FileScanEvent.objects.create(scan=record, operation=operation, status=record.status,
        format=kind, engine=record.engine, detail=(record.detail if detail is None else detail)[:500], actor=actor)


def has_exception(record, kind):
    return bool(record.status == 'rejected' and kind not in record.rejected_formats
        and record.exceptions.filter(format=kind, detection=record.detail, revoked_at__isnull=True).exists())


@transaction.atomic
def revoke_exceptions(record, *, actor=None, reason='Administrator revoked the exception.'):
    FileScan.objects.select_for_update().get(pk=record.pk)
    count = record.exceptions.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
    if count:
        record_event(record, 'revoke', actor=actor, detail=reason)
    return count


def approve_false_positive(digest, user, reason, *, expected_detection):
    """Authorize one known finding for these exact bytes after privileged review.

    The caller must reauthenticate the superuser. There is no blanket filename,
    uploader or signature allowlist, and structural checks cannot be waived.
    """
    if not user.is_active or not user.is_superuser:
        raise PermissionDenied
    reason = reason.strip()
    if not 20 <= len(reason) <= 400:
        raise ValidationError('Enter an investigation reason of 20 to 400 characters.')
    record = FileScan.objects.get(pk=digest)
    if record.detail != expected_detection:
        raise ValidationError('The finding changed. Review the current finding before granting an exception.')
    if record.status != 'rejected':
        raise ValidationError('Only a specific antivirus finding can receive an exception. Scanner errors and structural rejections cannot.')
    finding = record.detail
    if finding.startswith(('Heuristics.Limits.', 'Heuristics.Encrypted.')):
        raise ValidationError('Scanning limits and encrypted content cannot be overridden.')
    path = quarantine_path(digest)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ValidationError('The original is no longer held. Rescan the existing archive or ask the contributor to resubmit, then review again.') from error
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValidationError('The held bytes do not match this record. No exception was granted.')
    # Refresh the real verdict with a working, up-to-date scanner first.
    previous_attempts = record.attempts
    try:
        validate_file(data, record.filename, force=True)
    except ValidationError:
        pass
    with transaction.atomic():
        record = FileScan.objects.select_for_update().get(pk=digest)
        if record.attempts <= previous_attempts:
            raise ValidationError('A fresh scanner check did not complete. No exception was granted.')
        if record.status == 'clean':
            record_event(record, 'review', actor=user, detail='No exception needed after rescan. ' + reason)
            return False
        if record.status != 'rejected' or record.detail != finding:
            raise ValidationError('The scanner result changed or is unavailable. Review the new result before trying again.')
        kind = format_key(record.filename)
        try:
            inspect_file(data, kind)
        except ValidationError:
            record.rejected_formats = sorted(set(record.rejected_formats) | {kind})
            record.save(update_fields=['rejected_formats'])
            record_event(record, 'denied', kind=kind, actor=user, detail='Exception denied: structural checks failed. ' + reason)
            failure = True
        else:
            failure = False
            record.rejected_formats = [item for item in record.rejected_formats if item != kind]
            record.save(update_fields=['rejected_formats'])
            FileScanException.objects.update_or_create(scan=record, format=kind, defaults={
                'detection': finding, 'reason': reason, 'approved_by': user, 'revoked_at': None})
            record_event(record, 'exception', kind=kind, actor=user, detail=reason)
    if failure:
        raise ValidationError('Structural checks failed. This file remains blocked; an antivirus exception cannot waive those checks.')
    return True


def validate_file(data, filename, *, force=False):
    if not required():
        return
    if not data or len(data) > MAX_SCAN_BYTES:
        raise ValidationError('Files must be between 1 byte and 100 MB for security checks.')
    digest = hashlib.sha256(data).hexdigest()
    record, _ = FileScan.objects.get_or_create(sha256=digest, defaults={
        'filename': Path(filename).name[:255], 'size': len(data)})
    kind = format_key(filename)
    if not force and ((record.status == 'clean' and kind in record.formats and kind not in record.rejected_formats) or has_exception(record, kind)):
        return
    if (record.status == 'rejected' or kind in record.rejected_formats) and not force:
        raise ValidationError('This file was rejected by the security checks.')
    try:
        path = store_private(data, digest)
    except OSError as error:
        raise ValidationError('The private file-check queue is temporarily unavailable. Please try again later.') from error
    with transaction.atomic():
        record = FileScan.objects.select_for_update().get(pk=digest)
        if not force and ((record.status == 'clean' and kind in record.formats and kind not in record.rejected_formats) or has_exception(record, kind)):
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
            # A cleared or changed finding must not inherit an earlier waiver.
            if record.exceptions.filter(revoked_at__isnull=True).exclude(detection=detection).exists():
                revoke_exceptions(record, reason='Scanner finding changed or cleared; review is required for any new finding.')
            if detection:
                record.status = 'rejected'
                record.formats = []
                if has_exception(record, kind):
                    try:
                        inspect_file(data, kind)
                    except ValidationError as error:
                        structure_error = error
                        record.rejected_formats = sorted(set(record.rejected_formats) | {kind})
                        revoke_exceptions(record, reason='Structural validation failed during rescan.')
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
            if record.status == 'clean' and structure_error is None:
                path.unlink(missing_ok=True)
        record.checked = timezone.now()
        record.attempts += 1
        record.save()
        record_event(record, 'scan', kind=kind)
    log.info('File security check sha256=%s status=%s', digest, record.status)
    if structure_error:
        raise structure_error
    if record.status != 'clean' and not has_exception(record, kind):
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
    record = FileScan.objects.filter(sha256=digest).first() if digest else None
    kind = format_key(filename)
    return bool(record and ((record.status == 'clean' and kind in record.formats and kind not in record.rejected_formats)
                            or has_exception(record, kind)))


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
