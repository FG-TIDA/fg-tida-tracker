"""Editorial decisions and immutable IETF-style revisions, independent of Git tags."""
import hashlib
import json
import re
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ietf.doc.models import Document, State, DocEvent
from . import core
from .models import WorkItem, WorkItemRelease, GitHubJob

TRANSITIONS = {
    'submitted': {'draft', 'rejected', 'withdrawn'},
    'draft': {'review', 'withdrawn'},
    'review': {'draft', 'published', 'withdrawn'},
    'approved': {'draft', 'review', 'published', 'withdrawn'},  # Legacy editor-approved records.
    'published': {'withdrawn'}, 'rejected': {'withdrawn'}, 'withdrawn': set(),
}


def state_of(document):
    return document.states.filter(type_id=document.type_id).values_list('slug', flat=True).first() or 'submitted'


@transaction.atomic
def transition(document, state, actor, note):
    document = Document.objects.select_for_update().get(pk=document.pk)
    current = state_of(document)
    if state == current:
        return document
    if state not in TRANSITIONS.get(current, set()):
        raise ValidationError(f'Cannot move a work item from {current} to {state}.')
    item = WorkItem.objects.filter(document=document).first()
    if state == 'published' and item and item.repository_id and not item.releases.exists():
        raise ValidationError('Archive a GitHub release before marking this work item Published.')
    core.save_document(document, actor, note, state=State.objects.get(type_id=document.type_id, slug=state))
    if item and item.repository_id and state in {'draft', 'review', 'published', 'withdrawn'}:
        GitHubJob.objects.get_or_create(kind='state', work_item=item,
            key=f'state:{item.pk}:{document.docevent_set.latest("time").pk}')
    return document


def version_tuple(tag):
    match = re.fullmatch(r'v(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})(?:\.(0|[1-9][0-9]{0,5}))?', tag or '')
    if not match:
        raise ValidationError('Use a release tag such as v1.0 or v1.0.1.')
    return tuple(int(value or 0) for value in match.groups())


@transaction.atomic
def receive_build(repository, manifest, pdf, source_archive=None):
    """Caller authenticates the repository, workflow, ref and commit before this."""
    linked = WorkItem.objects.only('pk','document_id').get(repository=repository)
    if not linked.document_id:
        raise ValidationError('This work item has been deleted.')
    document = Document.objects.select_for_update().get(pk=linked.document_id)
    item = WorkItem.objects.select_for_update().get(pk=linked.pk)
    if not item.document_id or item.sync_mode != 'automatic':
        raise ValidationError('This work item is deleted, excluded or managed manually.')
    if state_of(document) not in {'draft', 'review'}:
        raise ValidationError('Builds are accepted only while Draft or Under review.')
    sha = manifest.get('commit', '')
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValidationError('A full Git commit SHA is required.')
    from .files import MAX_BYTES, extract_text
    if not 0 < len(pdf) <= MAX_BYTES or not pdf.startswith(b'%PDF-'):
        raise ValidationError('Provide a PDF of at most 10 MB.')
    extract_text(pdf, 'build.pdf')
    checksum = hashlib.sha256(pdf).hexdigest()
    if manifest.get('pdf_sha256') != checksum:
        raise ValidationError('PDF checksum does not match the manifest.')
    tag = manifest.get('tag', '')
    if not tag:
        if item.working_sha == sha:
            if item.working_checksum != checksum:
                raise ValidationError('This commit already has a different working build.')
            return 'unchanged', document.rev
        item.working_sha, item.working_pdf = sha, pdf
        item.working_checksum, item.working_manifest = checksum, manifest
        item.working_updated = timezone.now()
        item.save()
        DocEvent.objects.create(doc=document, by=core.actor_for(), type='changed_document', rev=document.rev,
            desc=f'GitHub working copy updated to {sha}. Archived revision {document.rev} is unchanged.')
        return 'working', document.rev
    version = version_tuple(tag)
    if not source_archive or len(source_archive) > 25 * 1024 * 1024:
        raise ValidationError('A source archive of at most 25 MB is required for a release.')
    if manifest.get('source_sha256') != hashlib.sha256(source_archive).hexdigest():
        raise ValidationError('Source archive checksum does not match the manifest.')
    from .github_archives import validate_source_archive
    validate_source_archive(source_archive)
    previous = item.releases.filter(tag=tag).first()
    if previous:
        if previous.commit_sha == sha and previous.checksum == checksum and previous.manifest == manifest:
            return 'unchanged', previous.revision
        raise ValidationError('A released tag cannot be replaced.')
    if any(tuple(release.version) >= version for release in item.releases.only('version')):
        raise ValidationError('Release tags must increase; duplicate or out-of-order tags are rejected.')
    core.save_document(document, core.actor_for(), f'GitHub release {tag}, commit {sha}.', data=pdf, filename='release.pdf')
    WorkItemRelease.objects.create(work_item=item, tag=tag, version=list(version), revision=document.rev,
        commit_sha=sha, checksum=checksum, manifest=manifest, source_archive=source_archive)
    return 'released', document.rev
