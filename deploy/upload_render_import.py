"""Stream private cutover files over SSH, without shell interpolation on Windows."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess


def upload(destination, source, name):
    source = Path(source)
    with source.open('rb') as incoming:
        expected = hashlib.file_digest(incoming, 'sha256').hexdigest()
        incoming.seek(0)
        remote = (f'umask 077; mkdir -p /tmp/fgtida-import && chmod 700 /tmp/fgtida-import '
                  f'&& cat > /tmp/fgtida-import/{name} && sha256sum /tmp/fgtida-import/{name}')
        result = subprocess.run(['ssh', '-T', destination, remote], stdin=incoming,
                                stdout=subprocess.PIPE, check=True, text=True)
    if result.stdout.split()[0] != expected:
        raise RuntimeError('The transferred file checksum does not match.')
    print(f'Transferred and verified {name}.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True, help='The user@host destination shown by Render Connect > SSH.')
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--files', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+@[A-Za-z0-9.-]+', args.destination):
        parser.error('Use only the user@host destination, without the ssh command or options.')
    upload(args.destination, args.fixture, 'fgtida.json')
    upload(args.destination, args.files, 'fgtida-files.zip')
