from pathlib import Path

from django.core.management.base import BaseCommand

from ietf.fgtida import core
from ietf.fgtida.github_attribution import source_attribution
from ietf.fgtida.models import DocumentVersion


class Command(BaseCommand):
    help = 'Index author, organization and contact fields from existing FG-TIDA archives without creating revisions.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, dry_run, **options):
        updated = missing = 0
        versions = DocumentVersion.objects.filter(document__in=core.documents(include_uncatalogued=True)).select_related('document')
        for version in versions.iterator():
            document = version.document
            if version.revision != document.rev:
                document = document.history_set.filter(rev=version.revision).order_by('-time', '-pk').first()
            if not document or not document.uploaded_filename or not Path(document.get_file_name()).is_file():
                missing += 1
                continue
            credits = source_attribution(document.text(size=65536) or '')
            metadata = {**version.source_metadata, **credits}
            if metadata != version.source_metadata:
                if not dry_run:
                    DocumentVersion.objects.filter(pk=version.pk, source_metadata=version.source_metadata).update(source_metadata=metadata)
                updated += 1
        self.stdout.write(f'{"Would update" if dry_run else "Updated"} {updated} version(s). Unavailable archives: {missing}.')
