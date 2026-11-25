"""Shared, scanned source preparation for initial submissions and revisions."""
import hashlib
from pathlib import Path, PurePosixPath

from django.core.exceptions import ValidationError


def prepare_source(method, *, upload=None, repository='', commit='', folder='', entry=''):
    from .files import MAX_BYTES
    from .source_packages import prepare_zip, github_snapshot, convert_word
    if method == 'github':
        if upload or not all((repository, commit, folder, entry)):
            raise ValidationError('For GitHub, provide the repository URL, full commit SHA, source folder and entry document. Do not upload a file.')
        archive, manifest = github_snapshot(repository, commit, folder, entry)
        content, filename = archive, 'github-source.zip'
    elif method in {'rst', 'word'}:
        if not upload:
            raise ValidationError('Choose your completed ZIP or Word document to upload.')
        if any((repository, commit, folder)):
            raise ValidationError('Repository URL, commit and source folder are for the GitHub route only. Clear them for an upload.')
        if upload.size > MAX_BYTES:
            raise ValidationError('Upload a file of at most 10 MB.')
        content, filename = upload.read(MAX_BYTES + 1), Path(upload.name).name
        if len(content) > MAX_BYTES:
            raise ValidationError('Upload a file of at most 10 MB.')
        if method == 'rst':
            if Path(filename).suffix.lower() != '.zip':
                raise ValidationError('Upload an RST source ZIP with all supporting files.')
            source_folder = str(PurePosixPath(entry).parent)
            archive, manifest = prepare_zip(content, entry, '' if source_folder == '.' else source_folder, complete=True)
        else:
            if Path(filename).suffix.lower() != '.docx':
                raise ValidationError('The Word route requires a .docx file.')
            archive, manifest = convert_word(content)
            entry = 'index.rst'
    else:
        raise ValidationError('Choose an RST ZIP, Word document or GitHub source.')
    from .submission_template import check_archive
    check_archive(archive, manifest, word=method == 'word')
    manifest.update(original_sha256=hashlib.sha256(content).hexdigest(), method=method)
    return {'content': content, 'filename': filename, 'archive': archive, 'manifest': manifest, 'entry': entry}
