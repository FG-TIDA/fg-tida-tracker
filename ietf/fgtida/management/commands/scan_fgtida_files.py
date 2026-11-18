"""Scan existing archives and pending private uploads without publishing new work."""
import hashlib
from pathlib import Path
import re
import time
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ietf.fgtida import core
from ietf.fgtida.file_safety import allowed, format_key, quarantine_path, required, scanner_version, scan_stream, validate_file, record_event, scanner_test_pattern, has_exception
from ietf.fgtida.models import (Contribution, DocumentFormat, DocumentVersion, FileScan,
    SessionResource, TrainingVideo, WorkItem, WorkItemRelease)


class Command(BaseCommand):
    help = 'Check the private scanner, retry quarantine, and scan existing FG-TIDA document versions and attachments.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20)
        parser.add_argument('--check-scanner', action='store_true')
        parser.add_argument('--force', action='store_true', help='Rescan files that already passed. Use without a limit large enough to cover the collection in maintenance.')
        parser.add_argument('--sha256', help='Only scan this exact file checksum, for a targeted reviewed recovery.')
        parser.add_argument('--reason', help='Review note for a targeted forced rescan (10 to 400 characters).')

    def handle(self, limit, check_scanner, force, **options):
        self.target = options.get('sha256')
        if self.target and not re.fullmatch(r'[0-9a-f]{64}', self.target):
            raise CommandError('Use the full lowercase SHA-256 checksum from Admin > File scans.')
        self.reason = (options.get('reason') or '').strip()
        self.review_recorded = False
        if self.target and force and not 10 <= len(self.reason) <= 400:
            raise CommandError('Provide --reason with a 10 to 400 character investigation note for this recovery.')
        if not required():
            raise CommandError('Set FGTIDA_UPLOAD_SCAN_REQUIRED=true before scanning files.')
        if limit < 1:
            raise CommandError('Choose a positive scan limit.')
        if not check_scanner:
            self.expire_private_uploads()
        try:
            version = scanner_version()
        except OSError as error:
            raise CommandError(str(error)) from error
        self.stdout.write('Private scanner ready. ' + version)
        if check_scanner:
            # Standard harmless antivirus test pattern, constructed only in memory.
            test_pattern = scanner_test_pattern()
            try:
                _, detection = scan_stream(test_pattern)
                _, clean_detection = scan_stream(b'FG-TIDA clean scanner connection test.\n')
            except OSError as error:
                raise CommandError('The private scanner test failed.') from error
            if not detection or clean_detection:
                raise CommandError('The scanner did not distinguish the antivirus test pattern from the clean control.')
            self.stdout.write('Antivirus detection and clean-control tests passed.')
            return
        self.attempted = self.passed = self.failed = 0
        self.processed = set()
        self.limit = limit
        self.force = force
        held = FileScan.objects.all() if self.target else FileScan.objects.filter(status__in=['pending', 'error'])
        if self.target:
            held = held.filter(pk=self.target)
        for record in held.order_by('created').iterator():
            path = quarantine_path(record.sha256)
            if path.is_file() and self.attempted < limit:
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() == record.sha256:
                    self.check(data, record.filename)
        documents = core.documents(include_uncatalogued=True)
        for version in DocumentVersion.objects.filter(document__in=documents).select_related('document').iterator():
            if self.attempted >= limit:
                break
            document = version.document
            snapshot = document if document.rev == version.revision else document.history_set.filter(rev=version.revision).order_by('-time', '-pk').first()
            path = Path(snapshot.get_file_name()) if snapshot and snapshot.uploaded_filename else None
            if path and path.is_file():
                data = path.read_bytes()
                if self.check(data, snapshot.uploaded_filename):
                    metadata = {**version.source_metadata, 'file_sha256': hashlib.sha256(data).hexdigest()}
                    DocumentVersion.objects.filter(pk=version.pk, source_metadata=version.source_metadata).update(source_metadata=metadata)
        for alternative in DocumentFormat.objects.filter(document__in=documents).iterator():
            if self.attempted >= limit:
                break
            data = bytes(alternative.content)
            if self.check(data, alternative.filename):
                DocumentFormat.objects.filter(pk=alternative.pk).update(sha256=hashlib.sha256(data).hexdigest())
        for item in Contribution.objects.defer('abstract', 'source_manifest').iterator():
            if self.attempted >= limit:
                break
            for data, filename in [(item.content, item.filename), (item.source_archive, 'source.zip')]:
                if data:
                    self.check(bytes(data), filename)
        for release in WorkItemRelease.objects.select_related('work_item').iterator():
            if self.attempted >= limit:
                break
            self.check(bytes(release.source_archive), 'source.tar.gz')
        for item in WorkItem.objects.exclude(working_sha='').iterator():
            if self.attempted >= limit:
                break
            if item.working_pdf:
                self.check(bytes(item.working_pdf), 'working.pdf')
        for model in (SessionResource, TrainingVideo):
            for resource in model.objects.filter(file_size__gt=0).iterator():
                if self.attempted >= limit:
                    break
                data = b''.join(bytes(part) for part in resource.file_chunks.order_by('position').values_list('content', flat=True))
                if self.check(data, resource.filename):
                    model.objects.filter(pk=resource.pk, file_version=resource.file_version).update(sha256=hashlib.sha256(data).hexdigest())
        from ietf.meeting.models import FloorPlan
        for plan in FloorPlan.objects.filter(meeting__type_id='tida').iterator():
            if self.attempted >= limit:
                break
            if plan.image and plan.image.storage.exists(plan.image.name):
                with plan.image.open('rb') as source:
                    self.check(source.read(), plan.image.name)
        self.stdout.write(f'New checks: {self.attempted}; passed: {self.passed}; held/rejected: {self.failed}. Existing work items and versions were preserved.')
        if self.target and self.force and not self.attempted:
            raise CommandError('The original bytes were not found. Ask the contributor to resubmit after admin review; no file was released.')

    def expire_private_uploads(self):
        try:
            root = quarantine_path('0' * 64).parent
            for path in root.iterdir():
                if re.fullmatch(r'[0-9a-f]{64}', path.name) and path.is_file() and time.time() - path.stat().st_mtime > 7 * 86400:
                    path.unlink(missing_ok=True)
                    for record in FileScan.objects.filter(pk=path.name).exclude(status='clean'):
                        record_event(record, 'expired', detail='Private upload expired; scanner verdict retained. Resubmit if the original is needed.')
        except OSError as error:
            raise CommandError('The private quarantine directory is unavailable.') from error

    def check(self, data, filename):
        if not data:
            return False
        digest = hashlib.sha256(data).hexdigest()
        if self.target and digest != self.target:
            return False
        key = (digest, format_key(filename))
        if key in self.processed:
            return allowed(data, filename)
        known = FileScan.objects.filter(pk=digest).first()
        if self.target and self.force and known and not self.review_recorded:
            record_event(known, 'review', detail='Operator recovery: ' + self.reason)
            self.review_recorded = True
        refresh = bool(known and known.checked and known.checked < timezone.now() - timedelta(days=1))
        if not self.force and not refresh and allowed(data, filename):
            return True
        if not self.force and known and ((known.status == 'rejected' and not has_exception(known, format_key(filename)))
                                       or format_key(filename) in known.rejected_formats):
            self.failed += 1
            return False
        if self.attempted >= self.limit:
            return False
        self.processed.add(key)
        self.attempted += 1
        try:
            validate_file(data, filename, force=self.force or refresh)
        except ValidationError:
            self.failed += 1
            return False
        self.passed += 1
        return True
