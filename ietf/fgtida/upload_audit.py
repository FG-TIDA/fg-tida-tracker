"""Read-only associations for held upload investigations; never release bytes."""
from pathlib import Path
from django.conf import settings
from django.db.models import Q
from .models import Contribution, DocumentVersion, DocumentFormat, ContributionRevisionOriginal


def related_contributions(scan, extra_ids=()):
    return Contribution.objects.filter(Q(source_manifest__original_sha256=scan.pk) |
        Q(source_manifest__sha256=scan.pk) |
        Q(pk__in=extra_ids) |
        Q(document__tida_versions__source_metadata__file_sha256=scan.pk) |
        Q(document__tida_formats__sha256=scan.pk) |
        Q(revision_originals__sha256=scan.pk)).distinct()


def original_contribution_index():
    import hashlib
    index = {}
    # Older submissions may predate checksum metadata. Hash stored bytes only;
    # this does not parse a document or execute any uploaded content.
    for pk, content, source in Contribution.objects.values_list('pk', 'content', 'source_archive').iterator(chunk_size=1):
        for data in (content, source):
            if data:
                index.setdefault(hashlib.sha256(bytes(data)).hexdigest(), set()).add(pk)
    return index


def scan_inventory(scan, extra_ids=()):
    # Inspect existence only: do not render, execute, download or publish files.
    private = Path(settings.FGTIDA_QUARANTINE_DIR) / scan.pk
    items = list(related_contributions(scan, extra_ids).values('pk', 'document__name', 'status'))
    archived = DocumentVersion.objects.filter(source_metadata__file_sha256=scan.pk).exists()
    alternative = DocumentFormat.objects.filter(sha256=scan.pk).exists()
    revision_original = ContributionRevisionOriginal.objects.filter(sha256=scan.pk).exists()
    return {'sha256': scan.pk, 'filename': scan.filename, 'status': scan.get_status_display(),
        'detail': scan.detail, 'created': scan.created.isoformat(),
        'checked': scan.checked.isoformat() if scan.checked else None,
        'last_storage_event': scan.events.filter(operation__in=['archived', 'expired', 'discard']).values('operation', 'detail').first(),
        'private_file_retained': private.is_file(), 'archive_reference': archived or alternative or revision_original,
        'contributions': items,
        'next_step': ('Review the linked contribution and its scan status.' if items else
            'No saved contribution association found. Check recent saved contributions and use --match-originals for older records before requesting resubmission. A scan cannot recreate a failed submission.')}
