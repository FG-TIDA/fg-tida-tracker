"""Inspect bounded source archives without extracting or executing their contents."""
import io
from pathlib import PurePosixPath
import tarfile
import subprocess
import sys
from django.core.exceptions import ValidationError


def validate_source_archive(data):
    try:
        subprocess.run([sys.executable,'-m','ietf.fgtida.github_archives'],input=data,
            capture_output=True,timeout=15,check=True)
    except subprocess.SubprocessError as error:
        raise ValidationError('Cannot safely read the source archive.') from error


def _validate(data):
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            size = 0
            for index, member in enumerate(archive):
                name = PurePosixPath(member.name)
                size += member.size
                if (index >= 5000 or size > 100 * 1024 * 1024 or name.is_absolute()
                        or '..' in name.parts or '\\' in member.name
                        or not (member.isfile() or member.isdir())):
                    raise ValueError('Unsafe source archive')
    except (tarfile.TarError, ValueError, OSError, EOFError) as error:
        raise ValidationError('Provide a valid source TAR.GZ without links, special files or unsafe paths.') from error


if __name__=='__main__':
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(10,10))
    _validate(sys.stdin.buffer.read(25*1024*1024+1))
