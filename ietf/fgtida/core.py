"""FG-TIDA configuration of native Datatracker models; no parallel data models."""
from pathlib import Path
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import transaction

from ietf.doc.models import Document, DocEvent, NewRevisionDocEvent, StateDocEvent
from ietf.group.models import Group
from ietf.meeting.models import Meeting
from ietf.person.models import Person

TYPE_LABELS = [('tida', 'FG-TIDA document'), ('agenda', 'Agenda'), ('minutes', 'Minutes'),
               ('slides', 'Slide'), ('charter', 'Charter'), ('tida-template', 'Template'),
               ('recording', 'Recording')]
DOCUMENT_TYPES = [slug for slug, label in TYPE_LABELS]
GROUP_ROLES = {'chair': 'Chair', 'cochair': 'Co-Chair', 'secr': 'Secretariat', 'editor': 'Editor', 'rapporteur': 'Rapporteur', 'member': 'Member'}
CATEGORIES = [('deliverables', 'Deliverables'), ('themes', 'Themes'), ('use-cases', 'Use cases'),
              ('templates', 'Templates'), ('resources', 'Resources')]


def categories():
    from ietf.name.models import DocTagName
    return [(row.slug[5:], row.name) for row in DocTagName.objects.filter(slug__startswith='tida-')
            .exclude(slug='tida-uncatalogued').order_by('name')]


def groups():
    # The parent relationship is authoritative; no dependency on repository names.
    selected = Group.objects.filter(acronym='tida').values_list('pk', flat=True)
    identifiers = set(selected)
    frontier = identifiers.copy()
    while frontier:
        frontier = set(Group.objects.filter(parent_id__in=frontier).values_list('pk', flat=True)) - identifiers
        identifiers.update(frontier)
    return Group.objects.filter(pk__in=identifiers).select_related('tida_configuration')


def documents(include_uncatalogued=False):
    records = Document.objects.filter(group__in=groups()).prefetch_related('tida_formats', 'tida_attribution__additional_types')
    return records if include_uncatalogued else records.exclude(tags__slug='tida-uncatalogued')


def template_package_url(doc):
    from urllib.parse import urlsplit
    url = urlsplit(doc.external_url or '')
    parts = url.path.strip('/').split('/')
    if url.scheme == 'https' and url.netloc.lower() == 'github.com' and len(parts) >= 2:
        return 'https://github.com/' + '/'.join(parts[:2]) + '/tree/main'
    return doc.external_url if url.scheme in ('https', 'http') else ''


def meetings():
    return Meeting.objects.filter(type_id='tida')


def actor_for(user=None):
    if user and user.is_authenticated:
        name = user.get_full_name() or user.username
        person, _ = Person.objects.get_or_create(user=user, defaults={'name': name, 'ascii': name})
    else:
        person, _ = Person.objects.get_or_create(name='FG-TIDA Tracker', ascii='FG-TIDA Tracker', user=None)
    return person


@transaction.atomic
def save_document(doc, actor, note, data=None, filename=None, state=None, session=None, version=None, source_metadata=None):
    """Use native DocEvent/DocHistory and the native document artifact paths."""
    if data is not None:
        from .files import extract_text
        text = extract_text(data, filename)
        if doc.pk:
            doc.rev = Document.objects.select_for_update().get(pk=doc.pk).rev
        from .versioning import declared_version, next_version
        public_version = next_version(doc, version if version is not None else declared_version(text))
        revision = int(doc.rev or '-1') + 1
        doc.rev = f'{revision:02d}'
        doc.uploaded_filename = f'{doc.name}-{doc.rev}{Path(filename).suffix.lower()}'
    if doc.pk is None:
        doc.time = timezone.now()
        doc.save(force_insert=True)
    if session is not None:
        from ietf.meeting.models import SessionPresentation
        SessionPresentation.objects.get_or_create(session=session, document=doc)
    for attribute in ('_cached_file_path', '_cached_base_name', '_cached_file_name', '_cached_extension'):
        doc.__dict__.pop(attribute, None)
    if state is not None:
        previous = doc.states.filter(type=state.type).first()
        if previous != state:
            doc.states.remove(*doc.states.filter(type=state.type))
            doc.states.add(state)
            StateDocEvent.objects.create(doc=doc, by=actor, type='changed_document', rev=doc.rev,
                state=state, state_type=state.type, desc=f'State changed to {state.name}. {note}')
    event_class = NewRevisionDocEvent if data is not None else DocEvent
    event = event_class.objects.create(doc=doc, by=actor, rev=doc.rev,
        type='new_revision' if data is not None else 'changed_document', desc=note)
    if data is not None:
        # All revisions retain distinct filenames; an old revision is never overwritten.
        folder = Path(doc.get_file_path())
        folder.mkdir(parents=True, exist_ok=True)
        artifact = folder / doc.uploaded_filename
        if artifact.exists():
            if artifact.read_bytes() != data:
                raise ValidationError('A different file already exists for this revision.')
        else:
            with artifact.open('xb') as target:
                target.write(data)
        if artifact.suffix.lower() != '.txt':
            artifact.with_suffix('.txt').write_text(text, encoding='utf-8')
        doc.store_bytes(doc.uploaded_filename, data)
        if artifact.suffix.lower() != '.txt':
            doc.store_str(artifact.with_suffix('.txt').name, text)
        doc.words = len(text.split())
    doc.save_with_history([event])
    if data is not None:
        from .models import DocumentVersion
        from .github_attribution import source_attribution
        import hashlib
        DocumentVersion.objects.create(document=doc, revision=doc.rev, version=public_version, recorded=event.time,
            source_metadata={**source_attribution(text), **(source_metadata or {}), 'file_sha256': hashlib.sha256(data).hexdigest()})
        doc.__dict__.pop('_version_labels',None)
        from .file_safety import retire_private_after_archive
        retire_private_after_archive(data, filename, archive_path=artifact)
    return doc
