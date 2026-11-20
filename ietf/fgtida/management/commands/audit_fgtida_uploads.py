"""Locate recent scan records even when they no longer have a blocked status."""
import json
from datetime import datetime, time, timezone
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q, Count
from django.conf import settings
from pathlib import Path
from ietf.fgtida.models import FileScan, Contribution
from ietf.fgtida.upload_audit import scan_inventory, original_contribution_index


class Command(BaseCommand):
    help = 'Read-only upload investigation by UTC date across every scan status; no private document content or account details.'

    def add_arguments(self, parser):
        parser.add_argument('--since', required=True, help='Inclusive UTC date YYYY-MM-DD.')
        parser.add_argument('--until', required=True, help='Exclusive UTC date YYYY-MM-DD.')
        parser.add_argument('--match-originals', action='store_true', help='Also hash stored contribution files to identify older submissions without checksum metadata. No files are rendered or released.')

    def handle(self, *args, **options):
        try:
            start, end = [datetime.combine(datetime.strptime(options[key], '%Y-%m-%d').date(), time.min, timezone.utc)
                          for key in ('since', 'until')]
        except ValueError as error:
            raise CommandError('Use YYYY-MM-DD dates.') from error
        if start >= end:
            raise CommandError('Until must be later than since (exclusive).')
        contributions = Contribution.objects.filter(created__gte=start, created__lt=end)
        originals = original_contribution_index() if options['match_originals'] else {}
        recent_ids = set(contributions.values_list('pk', flat=True))
        recent_digests = {digest for digest, identifiers in originals.items() if recent_ids.intersection(identifiers)}
        for manifest in contributions.values_list('source_manifest', flat=True):
            for key in ('original_sha256', 'sha256'):
                if manifest.get(key):
                    recent_digests.add(manifest[key])
        scans = FileScan.objects.filter(Q(created__gte=start, created__lt=end) |
            Q(checked__gte=start, checked__lt=end) |
            Q(events__created__gte=start, events__created__lt=end) |
            Q(pk__in=recent_digests)).distinct().order_by('created')
        quarantine = Path(settings.FGTIDA_QUARANTINE_DIR)
        print_data = {'mode': 'read-only', 'timezone': 'UTC', 'since': start.isoformat(), 'until_exclusive': end.isoformat(),
            'quarantine_directory': str(quarantine),
            'temporary_quarantine_warning': str(quarantine).startswith('/tmp/'),
            'all_scan_statuses': list(FileScan.objects.values('status').annotate(total=Count('pk'))),
            'recent_saved_contributions': list(contributions.values('pk', 'document__name', 'status')),
            'original_bytes_matched': options['match_originals'],
            'scans': [scan_inventory(scan, originals.get(scan.pk, ())) for scan in scans]}
        self.stdout.write(json.dumps(print_data, indent=2))
        self.stdout.write('No records or files were changed. Scan records are retained after rescanning. Files rejected before scanning may have no scan record; failed forms do not create contributions.')
