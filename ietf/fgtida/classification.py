"""Conservative source classification; subject matter never determines ownership."""
import re
from urllib.parse import urlsplit
from ietf.group.models import Group
from ietf.name.models import DocTagName
from ietf.doc.models import Document
from django.db import transaction
from . import core
from .models import WorkingGroupConfiguration, GitHubSource


# Reviewed public sources, 2026-11-06. Empty categories are intentional.
# The central use-case collection is not a WG0 repository. Charter documents
# have their own document type; that does not make them deliverables.
SOURCE_RULES = {
    'fg-tida/themes': ('tida', 'themes', '', 'https://github.com/FG-TIDA/themes#readme'),
    'fg-tida/use-cases': ('tida', 'use-cases', '', 'https://github.com/FG-TIDA/use-cases#readme'),
    'fg-tida/meetings': ('tida', '', '', ''),
    'fg-tida/marketing': ('tida', '', '', ''),
    'fg-tida/spec-template': ('tida', 'templates', '', 'https://github.com/FG-TIDA/spec-template'),
    'fg-tida/wg-charter-template': ('tida', 'templates', '', 'https://github.com/FG-TIDA/wg-charter-template'),
    'fg-tida/wg1-ra-aai': ('tidawg1', '', 'https://github.com/FG-TIDA/wg1-ra-aai#readme', ''),
    'fg-tida-wg1/wg1-cdra-charter': ('tidawg1', '', 'https://github.com/fg-tida-wg1/wg1-cdra-charter', ''),
    'fg-tida-wg2/wg2-ibea-charter': ('tidawg2', '', 'https://github.com/fg-tida-wg2/wg2-ibea-charter', ''),
}


def public_github_url(value):
    url = urlsplit(value or '')
    return url.scheme == 'https' and url.netloc.lower() == 'github.com' and bool(url.path.strip('/'))


def resolve(repository, key, source_url='', metadata=None):
    metadata = metadata or {}
    group = repository.group
    evidence = repository.group_evidence
    # A native owner is still necessary, but FG-TIDA ownership is not a WG
    # assignment. Never infer wg0 from "use cases", or Incubation from "themes".
    if group.acronym != 'tida' and not public_github_url(evidence):
        group = Group.objects.get(acronym='tida')
        evidence = ''
    labels = metadata.get('labels', [])
    numbers = {int(match[1]) for label in labels if isinstance(label, str)
        and (match := re.fullmatch(r'wg(\d+)', label, re.I))}
    if numbers and repository.mode == 'issues':
        explicit = WorkingGroupConfiguration.objects.filter(number__in=numbers, group__in=core.groups()).select_related('group')
        if len(numbers) == 1 and explicit.count() == 1 and public_github_url(source_url):
            group = explicit.first().group
            evidence = source_url
        else:
            group = Group.objects.get(acronym='tida')
            evidence = ''
    category = repository.category if public_github_url(repository.category_evidence) and repository.category in dict(core.CATEGORIES) else ''
    if key.startswith('attachment:'):
        category = ''  # Supporting files are not themselves theme/use-case proposals.
    result = {'group': group.acronym, 'group_evidence': evidence if group.acronym != 'tida' else '',
        'category': category, 'category_evidence': repository.category_evidence if category else ''}
    return group, category, {**metadata, 'classification': result}


def refresh_document(doc, group, category, source_url):
    category_tags = ['tida-'+slug for slug, _ in core.CATEGORIES]
    old = set(doc.tags.filter(slug__in=category_tags).values_list('slug', flat=True))
    desired = {'tida-'+category} if category else set()
    changed = doc.group_id != group.pk or old != desired
    if changed:
        doc.group = group
        doc.tags.remove(*old)
        for slug in desired:
            DocTagName.objects.get_or_create(slug=slug, defaults={'name': dict(core.CATEGORIES).get(slug[5:], slug)})
            doc.tags.add(slug)
        core.save_document(doc, core.actor_for(), 'Corrected source classification using recorded GitHub evidence; no new file revision. '+source_url)
    return changed


@transaction.atomic
def refresh(source, group, category, metadata):
    """Repair metadata only: preserve file bytes, revision labels and manual edits."""
    if not source.document_id:
        return False
    # Match editor/import lock order and re-read the manual policy after locking;
    # do not write an older document snapshot over a concurrent editor change.
    doc = Document.objects.select_for_update().get(pk=source.document_id)
    source = GitHubSource.objects.select_for_update().get(pk=source.pk)
    if source.document_id != doc.pk or source.sync_mode != 'automatic' or (hasattr(doc, 'tida_work_item') and doc.tida_work_item.sync_mode != 'automatic'):
        return False
    changed = refresh_document(doc, group, category, source.source_url)
    if source.source_metadata != metadata:
        source.source_metadata = metadata
        source.save(update_fields=['source_metadata'])
    return changed
