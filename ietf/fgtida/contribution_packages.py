"""Reviewed, versioned completion of older contribution source packages."""
import hashlib
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from . import core
from .models import Contribution
from .source_packages import unpack, pack, prepare_zip
from .word_scaffold import scaffold, complete_rst_source


def inspect_package(item):
    from .file_safety import preview_source, allowed
    if not item.document_id:
        return {'state': 'no_document'}
    data, filename = preview_source(item.document)
    if not data or not filename.lower().endswith('.zip'):
        return {'state': 'no_current_zip'}
    if not allowed(data, filename):
        return {'state': 'awaiting_security_checks'}
    files = unpack(data)
    if not any(name.endswith('.rst') for name in files):
        return {'state': 'entry_document_requires_review'}
    missing = sorted(set(scaffold()[1]) - set(files))
    if not missing:
        return {'state': 'complete'}
    retained = bytes(item.source_archive or item.content)
    if not retained or retained != data:
        return {'state': 'later_source_requires_review', 'missing': missing}
    return {'state': 'incomplete', 'missing': missing, 'files': len(files)}


@transaction.atomic
def complete_package(pk, user=None, reason=''):
    if user is not None and (not user.is_active or not user.is_staff or not user.has_perm('doc.change_document')):
        raise PermissionDenied
    if len(reason.strip()) < 10:
        raise ValidationError('Provide an administrator review reason of at least 10 characters.')
    item = Contribution.objects.select_for_update(of=('self',)).select_related('document').get(pk=pk)
    from .catalog_repairs import use_case_documents
    if item.document_id and use_case_documents().filter(pk=item.document_id).exists():
        raise ValidationError('Imported use cases are excluded from specification-package completion.')
    report = inspect_package(item)
    if report['state'] == 'complete':
        return False
    if report['state'] != 'incomplete':
        raise ValidationError('Package cannot be completed automatically: ' + report['state'])
    if item.filename.lower().endswith('.docx') and item.content and item.submission_method in {'word', 'legacy'}:
        from .contributions import convert_existing_word
        return convert_existing_word(pk, user, rebuild=True, reason=reason)
    previous = bytes(item.source_archive or item.content)
    files = unpack(previous)
    entry = item.source_manifest.get('entry_document', '')
    if not entry:
        rst = [name for name in files if name.endswith('.rst')]
        entry = 'index.rst' if 'index.rst' in files else rst[0] if len(rst) == 1 else ''
    if not entry:
        raise ValidationError('Specify the entry document before completing a multi-chapter package.')
    merged, metadata = complete_rst_source(files, entry)
    archive, manifest = prepare_zip(pack(merged), entry)
    if archive == previous:
        return False
    # Do not overwrite old archives or alter the original upload, references,
    # uploader, group, source commit, lifecycle decision or notifications.
    from .versioning import next_version
    old = hashlib.sha256(previous).hexdigest()
    core.save_document(item.document, core.actor_for(user),
        'Specification build assets added; original source bytes preserved. ' + reason.strip(),
        data=archive, filename='source.zip', version=next_version(item.document),
        source_metadata={'package_completion': {**metadata, 'previous_package_sha256': old}})
    item.source_archive = archive
    item.source_manifest = {**item.source_manifest, **manifest, **metadata, 'previous_package_sha256': old}
    item.save(update_fields=['source_archive', 'source_manifest'])
    return True
