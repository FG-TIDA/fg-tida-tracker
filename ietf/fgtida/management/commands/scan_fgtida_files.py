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
        parser.add_argument('--pending-only', action='store_true', help='Retry retained pending/error files without walking existing document/media archives.')
        parser.add_argument('--force', action='store_true', help='Rescan files that already passed. Use without a limit large enough to cover the collection in maintenance.')
        parser.add_argument('--sha256', help='Only scan this exact file checksum, for a targeted reviewed recovery.')
        parser.add_argument('--reason', help='Review note for a targeted forced rescan (10 to 400 characters).')

    def handle(self, limit, check_scanner, force, **options):
        self.target = options.get('sha256')
        if options.get('pending_only') and (force or self.target):
            raise CommandError('Use --pending-only without --force or --sha256.')
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
        if options.get('pending_only'):
            self.stdout.write(f'Pending checks: {self.attempted}; passed: {self.passed}; held/rejected: {self.failed}. Existing archives were not traversed.')
            return
        self.known_scans = {scan.pk: scan for scan in FileScan.objects.only(
            'sha256', 'status', 'checked', 'formats', 'rejected_formats', 'detail').iterator()}
        documents = core.documents(include_uncatalogued=True)
        for version in DocumentVersion.objects.filter(document__in=documents).select_related('document').iterator():
            if self.attempted >= limit:
                break
            document = version.document
            snapshot = document if document.rev == version.revision else document.history_set.filter(rev=version.revision).order_by('-time', '-pk').first()
            if snapshot and not self.needs_bytes(version.source_metadata.get('file_sha256'), snapshot.uploaded_filename):
                continue
            path = Path(snapshot.get_file_name()) if snapshot and snapshot.uploaded_filename else None
            if path and path.is_file():
                data = path.read_bytes()
                if self.check(data, snapshot.uploaded_filename):
                    metadata = {**version.source_metadata, 'file_sha256': hashlib.sha256(data).hexdigest()}
                    DocumentVersion.objects.filter(pk=version.pk, source_metadata=version.source_metadata).update(source_metadata=metadata)
                    from ietf.fgtida.file_safety import retire_private_after_archive
                    retire_private_after_archive(data, snapshot.uploaded_filename, archive_path=path)
        for alternative in DocumentFormat.objects.filter(document__in=documents).defer('content').iterator():
            if self.attempted >= limit:
                break
            if not self.needs_bytes(alternative.sha256, alternative.filename):
                continue
            data = bytes(alternative.content)
            if self.check(data, alternative.filename):
                DocumentFormat.objects.filter(pk=alternative.pk).update(sha256=hashlib.sha256(data).hexdigest())
        from ietf.fgtida.models import ContributionRevisionOriginal
        for original in ContributionRevisionOriginal.objects.all().iterator(chunk_size=1):
            if self.attempted >= limit:
                break
            if self.needs_bytes(original.sha256, original.filename):
                self.check(bytes(original.content), original.filename)
        for item in Contribution.objects.defer('abstract', 'content', 'source_archive').iterator():
            if self.attempted >= limit:
                break
            for field, filename, digest in [('content', item.filename, item.source_manifest.get('original_sha256')),
                                           ('source_archive', 'source.zip', item.source_manifest.get('sha256'))]:
                if not self.needs_bytes(digest, filename):
                    continue
                data = getattr(item, field)
                if data:
                    if self.check(bytes(data), filename):
                        from ietf.fgtida.file_safety import retire_private_after_archive
                        retire_private_after_archive(bytes(data), filename, contribution_id=item.pk)
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
                if not self.needs_bytes(resource.sha256, resource.filename):
                    continue
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

    def needs_bytes(self, digest, filename):
        """Scheduling hint only. Downloads still hash actual bytes and check AV."""
        if self.force or self.target or not digest:
            return True
        known = self.known_scans.get(digest)
        if not known:
            return True
        kind = format_key(filename or '')
        approved = kind in known.formats and kind not in known.rejected_formats
        if known.status == 'admin' and approved:
            return False
        if known.status == 'clean' and approved and known.checked and known.checked >= timezone.now() - timedelta(days=1):
            return False
        if kind in known.rejected_formats or (known.status == 'rejected' and not has_exception(known, kind)):
            return False
        return True

    def expire_private_uploads(self):
        try:
            root = quarantine_path('0' * 64).parent
            for path in root.iterdir():
                if re.fullmatch(r'[0-9a-f]{64}', path.name) and path.is_file() and time.time() - path.stat().st_mtime > 7 * 86400:
                    path.unlink(missing_ok=True)
                    for record in FileScan.objects.filter(pk=path.name):
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
        if known and known.status == 'admin':
            return allowed(data, filename)
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
