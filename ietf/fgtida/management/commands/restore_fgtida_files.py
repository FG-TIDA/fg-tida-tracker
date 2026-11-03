"""Restore only authenticated, confined release artifacts onto the mounted disk."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
from zipfile import ZipFile, BadZipFile

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Verify and restore the FG-TIDA file export to FGTIDA_DATA_DIR, without overwriting different files.'

    def add_arguments(self, parser):
        parser.add_argument('--archive', required=True)

    def handle(self, *args, **options):
        root = Path(settings.FGTIDA_DATA_DIR).resolve()
        allowed = {'documents', 'proceedings', 'media', 'charters', 'internet-drafts', 'rfc'}
        try:
            with ZipFile(options['archive']) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)) or len(names) > 100000:
                    raise ValueError('Duplicate or excessive archive entries.')
                if archive.getinfo('manifest.sha256.json').file_size > 16 * 1024 * 1024:
                    raise ValueError('The artifact manifest exceeds the restore size limit.')
                manifest = json.loads(archive.read('manifest.sha256.json'))
                if not isinstance(manifest, dict) or set(names) != set(manifest) | {'manifest.sha256.json'}:
                    raise ValueError('The file manifest does not match the archive.')
                # Validate every path and checksum before changing any files.
                for name, digest in manifest.items():
                    path = PurePosixPath(name)
                    target = (root / name).resolve()
                    if ('\\' in name or path.is_absolute() or '..' in path.parts
                            or not path.parts or path.parts[0] not in allowed
                            or not target.is_relative_to(root)):
                        raise ValueError('Unsafe artifact path.')
                    if archive.getinfo(name).file_size > 512 * 1024 * 1024:
                        raise ValueError('An artifact exceeds the restore size limit.')
                    with archive.open(name) as incoming:
                        if hashlib.file_digest(incoming, 'sha256').hexdigest() != digest:
                            raise ValueError('An artifact checksum does not match.')
                    if target.exists():
                        with target.open('rb') as existing:
                            if hashlib.file_digest(existing, 'sha256').hexdigest() != digest:
                                raise ValueError('A different file already exists at the destination.')
                for name in manifest:
                    target = (root / name).resolve()
                    if target.exists():
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as outgoing:
                            temporary = Path(outgoing.name)
                            with archive.open(name) as incoming:
                                while block := incoming.read(1024 * 1024):
                                    outgoing.write(block)
                        temporary.chmod(0o600)
                        temporary.replace(target)
                    finally:
                        if temporary and temporary.exists():
                            temporary.unlink()
        except (OSError, ValueError, KeyError, BadZipFile) as error:
            raise CommandError(f'Artifact restore refused: {error}') from error
        self.stdout.write(self.style.SUCCESS(f'Verified and restored {len(manifest)} artifacts.'))
