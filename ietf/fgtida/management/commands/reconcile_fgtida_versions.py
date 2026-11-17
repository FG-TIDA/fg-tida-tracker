from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from ietf.fgtida import core
from ietf.fgtida.models import DocumentVersion
from ietf.fgtida.versioning import correct_version, declared_version, validate_correction


class Command(BaseCommand):
    help = 'Align non-release archive labels with explicit source versions. Archive contents and native revision IDs are preserved.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, apply, **options):
        changed = skipped = 0
        records = DocumentVersion.objects.filter(document__in=core.documents(include_uncatalogued=True)).select_related('document').order_by('document_id', '-revision')
        for record in records.iterator():
            document = record.document
            snapshot = document if document.rev == record.revision else document.history_set.filter(rev=record.revision).order_by('-time', '-pk').first()
            if not snapshot or not snapshot.uploaded_filename or not Path(snapshot.get_file_name()).is_file():
                continue
            version = declared_version(snapshot.text() or '')
            if not version or version == record.version:
                continue
            try:
                previous = record.version
                validate_correction(record, version)
                if apply:
                    correct_version(record, version, core.actor_for())
            except ValidationError as error:
                skipped += 1
                self.stdout.write(f'{document.name}: ' + ' '.join(error.messages))
            else:
                changed += 1
                self.stdout.write(f'{document.name}: {previous} -> {version}')
        self.stdout.write(f'{"Corrected" if apply else "Would correct"} {changed} label(s). Require review: {skipped}.')
