"""Encrypt/decrypt a release ZIP with AES-256-GCM; keep the raw key separately."""
import argparse
from pathlib import Path
import os
import tempfile
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b'FGTIDA1\0'
CHUNK = 1024 * 1024


def transform(source, output, key_path, encrypt=False):
    source, output, key_path = map(Path, (source, output, key_path))
    if output.exists():
        raise ValueError('Output already exists; refusing to overwrite it.')
    if encrypt and not key_path.exists():
        with key_path.open('xb') as target:
            os.chmod(key_path, 0o600)
            target.write(os.urandom(32))
    key = key_path.read_bytes()
    if len(key) != 32:
        raise ValueError('Expected a 32-byte encryption key.')
    temporary = None
    try:
        with source.open('rb') as incoming, tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as outgoing:
            temporary = Path(outgoing.name)
            os.chmod(temporary, 0o600)
            if encrypt:
                nonce = os.urandom(12)
                operation = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
                operation.authenticate_additional_data(MAGIC)
                outgoing.write(MAGIC + nonce)
                remaining = source.stat().st_size
            else:
                if incoming.read(len(MAGIC)) != MAGIC:
                    raise ValueError('Not an FG-TIDA encrypted release archive.')
                nonce = incoming.read(12)
                incoming.seek(-16, 2)
                tag = incoming.read(16)
                incoming.seek(len(MAGIC) + 12)
                remaining = source.stat().st_size - len(MAGIC) - 12 - 16
                if remaining < 0:
                    raise ValueError('Truncated encrypted archive.')
                operation = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
                operation.authenticate_additional_data(MAGIC)
            while remaining:
                data = incoming.read(min(CHUNK, remaining))
                if not data:
                    raise ValueError('Truncated input.')
                outgoing.write(operation.update(data))
                remaining -= len(data)
            outgoing.write(operation.finalize())
            if encrypt:
                outgoing.write(operation.tag)
        # Exclusive link prevents concurrent overwrite; publish only authenticated output.
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['encrypt', 'decrypt'])
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--key', required=True)
    arguments = parser.parse_args()
    transform(arguments.input, arguments.output, arguments.key, arguments.mode == 'encrypt')
    print('Archive verified and written. Key contents were not displayed.')
