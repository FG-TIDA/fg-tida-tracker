"""Stream private cutover files over SSH, without shell interpolation on Windows."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess


def upload(destination, source, name, identity_file=None):
    if name not in {'fgtida.json', 'fgtida-files.zip', 'participants.csv'}:
        raise ValueError('Unsupported import filename.')
    source = Path(source)
    ssh = ['ssh', '-T', '-o', 'UpdateHostKeys=no', '-o', 'StrictHostKeyChecking=yes']
    if identity_file:
        ssh += ['-o', 'IdentitiesOnly=yes', '-i', str(identity_file)]
    with source.open('rb') as incoming:
        expected = hashlib.file_digest(incoming, 'sha256').hexdigest()
        incoming.seek(0)
        remote = (f'umask 077; mkdir -p /tmp/fgtida-import && chmod 700 /tmp/fgtida-import '
                  f'&& cat > /tmp/fgtida-import/{name} && sha256sum /tmp/fgtida-import/{name}')
        result = subprocess.run([*ssh, destination, remote], stdin=incoming,
                                stdout=subprocess.PIPE, check=True, text=True)
    fields = result.stdout.split()
    if not fields or fields[0] != expected:
        raise RuntimeError('The transferred file checksum does not match.')
    print(f'Transferred and verified {name}.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True, help='The user@host destination shown by Render Connect > SSH.')
    parser.add_argument('--fixture')
    parser.add_argument('--files')
    parser.add_argument('--participants', help='Transfer an authorized participant CSV privately, without uploading it to GitHub.')
    parser.add_argument('--identity-file', type=Path, help='Private SSH key path; the passphrase is requested by SSH, never passed as an argument.')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+@[A-Za-z0-9.-]+', args.destination):
        parser.error('Use only the user@host destination, without the ssh command or options.')
    if args.participants and (args.fixture or args.files):
        parser.error('Use --participants separately from the fixture/files cutover transfer.')
    if args.participants and not Path(args.participants).is_file():
        parser.error('The participant CSV file does not exist.')
    if not args.participants and (not args.fixture or not args.files or not all(Path(path).is_file() for path in (args.fixture, args.files))):
        parser.error('Both local export files must exist before transfer.')
    if args.identity_file and not args.identity_file.is_file():
        parser.error('The specified SSH identity file does not exist.')
    if args.participants:
        upload(args.destination, args.participants, 'participants.csv', args.identity_file)
    else:
        upload(args.destination, args.fixture, 'fgtida.json', args.identity_file)
        upload(args.destination, args.files, 'fgtida-files.zip', args.identity_file)
