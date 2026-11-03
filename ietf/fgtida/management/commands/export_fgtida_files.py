import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from ietf.fgtida import core


class Command(BaseCommand):
    help = 'Export current/historical FG-TIDA uploads and release-fixture photos with SHA-256 checksums.'

    def add_arguments(self, parser):
        parser.add_argument('--fixture', required=True)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        fixture = json.loads(Path(options['fixture']).read_text())
        copies = {}
        for doc in core.documents(include_uncatalogued=True):
            for snapshot in [doc, *doc.history_set.exclude(uploaded_filename='')]:
                if not snapshot.uploaded_filename:
                    continue
                source = Path(snapshot.get_file_name()).resolve()
                roots = [(Path(settings.DOCUMENT_PATH_PATTERN.format(doc=snapshot)).resolve(), f'documents/{snapshot.type_id}'),
                         (Path(settings.AGENDA_PATH).resolve(), 'proceedings'),
                         (Path(settings.CHARTER_PATH).resolve(), 'charters')]
                destination = None
                for root, target in roots:
                    if source.is_relative_to(root):
                        destination = Path(target) / source.relative_to(root)
                        break
                if destination is None or not source.is_file():
                    raise CommandError('A referenced revision is missing or outside the supported artifact paths.')
                copies[destination.as_posix()] = source
                if source.suffix != '.txt' and source.with_suffix('.txt').is_file():
                    copies[destination.with_suffix('.txt').as_posix()] = source.with_suffix('.txt')
        for item in fixture:
            if item['model'] not in {'person.person', 'meeting.floorplan'}:
                continue
            for field in (('image',) if item['model'] == 'meeting.floorplan' else ('photo', 'photo_thumb')):
                name = item['fields'].get(field)
                if name:
                    root = Path(settings.MEDIA_ROOT).resolve()
                    source = (root / name).resolve()
                    if not source.is_relative_to(root) or not source.is_file():
                        raise CommandError('A profile photo is missing or outside the media directory.')
                    copies['media/' + source.relative_to(root).as_posix()] = source
        output = Path(options['output'])
        output.parent.mkdir(parents=True, exist_ok=True)
        manifest = {}
        with ZipFile(output, 'x', ZIP_DEFLATED) as archive:
            for target, source in sorted(copies.items()):
                data = source.read_bytes()
                manifest[target] = hashlib.sha256(data).hexdigest()
                archive.writestr(target, data)
            archive.writestr('manifest.sha256.json', json.dumps(manifest, indent=2))
        output.chmod(0o600)
        self.stdout.write(self.style.SUCCESS(f'Exported {len(copies)} files with integrity checksums.'))
