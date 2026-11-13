"""Bounded source snapshots. Submitted configuration is stored, never executed."""
import hashlib
import io
import posixpath
import re
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import PurePosixPath

import requests
from django.core.exceptions import ValidationError

MAX_UPLOAD = 10 * 1024 * 1024
MAX_EXPANDED = 30 * 1024 * 1024
MAX_FILES = 500
SOURCE_SUFFIXES = {'.rst', '.txt', '.bib', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.pdf', '.drawio', '.puml', '.mmd', '.csv', '.json', '.yaml', '.yml', '.toml', '.css', '.md'}


def safe_path(value):
    if (not value or len(value) > 255 or '\\' in value or ':' in value
            or any(ord(c) < 32 for c in value) or value.startswith('/')
            or any(p in {'', '.', '..'} for p in value.split('/'))):
        raise ValidationError('Use a relative file path without .., backslashes or drive letters.')
    return value


def unpack(data):
    if len(data) > MAX_UPLOAD:
        raise ValidationError('Upload a source ZIP of at most 10 MB.')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES or sum(e.file_size for e in entries) > MAX_EXPANDED:
                raise ValidationError('Source package exceeds 500 entries or 30 MB expanded.')
            files = {}
            seen = set()
            for entry in entries:
                name = safe_path(entry.filename.rstrip('/'))
                if (name.casefold() in seen or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1
                        or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                    raise ValidationError('Use ordinary ZIP compression without duplicate paths, symbolic links or encrypted entries.')
                seen.add(name.casefold())
                if not entry.is_dir():
                    files[name] = archive.read(entry)
            return files
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as error:
        raise ValidationError('Cannot read this ZIP. Upload an ordinary, unencrypted source ZIP.') from error


def pack(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
    data = stream.getvalue()
    if len(data) > MAX_UPLOAD:
        raise ValidationError('The prepared source package exceeds 10 MB.')
    return data


def validate_sources(files, entry):
    safe_path(entry)
    if entry not in files or not entry.endswith('.rst'):
        raise ValidationError('The entry document must name an RST file inside the package.')
    for name, data in files.items():
        path = PurePosixPath(name)
        if (any(part.startswith('.') for part in path.parts) or
                (path.suffix.lower() not in SOURCE_SUFFIXES and path.name != 'conf.py')):
            raise ValidationError(f'Unsupported source file: {name}. Include documentation and assets only; remove hidden files, programs and installed environments.')
        if path.suffix == '.rst':
            try:
                text = data.decode('utf-8-sig')
            except UnicodeError as error:
                raise ValidationError(f'{name} must use UTF-8 text encoding.') from error
            if len(data) > 500000:
                raise ValidationError('Each RST chapter must be at most 500 KB.')
            for target in re.findall(r'^\s*\.\.\s+(?:\|[^|\n]+\|\s+)?(?:image|figure|include|literalinclude)::\s*(\S+)', text, re.M):
                resolved = posixpath.normpath(posixpath.join(str(path.parent), target))
                if target.startswith('/') or ':' in target or '\\' in target or resolved not in files:
                    raise ValidationError(f'{name} references a missing or external file: {target}. Include the file in the source package.')


def prepare_zip(data, entry, folder=''):
    files = unpack(data)
    if folder:
        safe_path(folder)
        prefix = folder + '/'
        if not entry.startswith(prefix):
            raise ValidationError('The entry document must be inside the source folder.')
        files = {name[len(prefix):]: value for name, value in files.items() if name.startswith(prefix)}
        entry = entry[len(prefix):]
    validate_sources(files, entry)
    manifest = {'entry_document': entry, 'files': {
        name: hashlib.sha256(value).hexdigest() for name, value in sorted(files.items())}}
    result = pack(files)
    manifest['sha256'] = hashlib.sha256(result).hexdigest()
    return result, manifest


def github_snapshot(url, commit, folder, entry):
    from .github_client import repository_name
    repo = repository_name(url)
    if not re.fullmatch('[0-9a-fA-F]{40}', commit or ''):
        raise ValidationError('Copy the full 40-character commit SHA from your repository.')
    safe_path(folder)
    safe_path(entry)
    commit = commit.lower()
    # No App token: contributors submit public personal repositories, not App installations.
    try:
        start = time.monotonic()
        with requests.get(f'https://codeload.github.com/{repo}/zip/{commit}',
                          timeout=(5, 30), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValidationError('Cannot fetch this public GitHub repository at the supplied commit.')
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > MAX_UPLOAD or time.monotonic() - start > 45:
                    raise ValidationError('GitHub snapshot exceeds the 10 MB or download time limit. Submit a documentation-only ZIP instead.')
    except requests.RequestException as error:
        raise ValidationError('GitHub is unavailable. Try again or submit the RST ZIP.') from error
    files = unpack(bytes(data))
    roots = {name.split('/')[0] for name in files}
    if len(roots) != 1:
        raise ValidationError('Unexpected GitHub archive structure.')
    root = roots.pop() + '/'
    files = {name[len(root):]: value for name, value in files.items()}
    prepared, manifest = prepare_zip(pack(files), entry, folder)
    manifest.update(repository='https://github.com/' + repo, commit=commit, source_folder=folder)
    return prepared, manifest


def convert_word(data):
    if len(data) > MAX_UPLOAD:
        raise ValidationError('Upload a Word document of at most 10 MB.')
    try:
        result = subprocess.run([sys.executable, '-m', 'ietf.fgtida.word_worker'],
                                input=data, capture_output=True, check=True, timeout=35)
        return prepare_zip(result.stdout, 'index.rst')
    except (subprocess.SubprocessError, OSError) as error:
        raise ValidationError('Word conversion failed. Resolve tracked changes, use embedded images and standard Word headings, then retry. Contact the secretariat if conversion still fails.') from error


def package_text(data):
    files = unpack(data)
    entry = next((name for name in files if name.endswith('.rst')), '')
    validate_sources(files, entry)
    return '\n\n'.join(value.decode('utf-8-sig') for name, value in sorted(files.items()) if name.endswith('.rst'))[:2000000]
