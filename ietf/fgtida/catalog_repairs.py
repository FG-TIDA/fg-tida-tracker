"""Repair only evidenced use-case ownership and explicit missing attribution."""
from collections import Counter
from django.db import transaction
from django.db.models import Q
from . import core
from .models import (Contribution, ContributionSequence, DocumentAttribution, PublicDocumentType, GitHubSource)
from .github_attribution import source_attribution, CREDIT_FIELDS


def use_case_documents():
    sources = GitHubSource.objects.filter(repository__name='use-cases',
        repository__account__organization__in=['FG-TIDA', 'fg-tida-wg0']).exclude(source_key__startswith='attachment:')
    return core.documents(include_uncatalogued=True).filter(Q(tags__slug='tida-use-cases') |
        Q(tida_attribution__public_type__name='Use case') | Q(tida_attribution__additional_types__name='Use case') |
        Q(pk__in=sources.values('document_id')) |
        Q(external_url__istartswith='https://github.com/fg-tida-wg0/use-cases/issues/') |
        Q(external_url__istartswith='https://github.com/FG-TIDA/use-cases/issues/')).distinct()


@transaction.atomic
def repair_catalog(apply=False):
    from ietf.group.models import Group
    from .document_types import assign_source_type
    from .file_safety import safe_document_text
    from .credits import clean_values, legacy_import_fields
    stats = Counter()
    wg0 = Group.objects.get(acronym='tidawg0', parent__acronym='tida')
    identifiers = list(use_case_documents().values_list('pk', flat=True))
    use_cases = core.documents(include_uncatalogued=True).filter(pk__in=identifiers).order_by('pk')
    if apply:
        use_cases = use_cases.select_for_update()
    for doc in use_cases:
        stats['use_cases_reviewed'] += 1
        if doc.group_id != wg0.pk:
            stats['wg0_assignments_corrected'] += 1
            if apply:
                doc.group = wg0
                core.save_document(doc, core.actor_for(), 'Use-case ownership corrected to wg0 under the administrator’s explicit policy.')
        if apply:
            # Reuse the contribution ID and archive; move an incorrect route
            # into the same serialized WG0 numbering sequence.
            for item in Contribution.objects.filter(document=doc).exclude(group=wg0):
                old_prefix = item.number_prefix
                if old_prefix == 'FG-TIDA-WG0':
                    Contribution.objects.filter(pk=item.pk).update(group=wg0)
                    stats['contribution_routes_corrected'] += 1
                    continue
                sequences = {prefix: ContributionSequence.lock(prefix) for prefix in sorted({old_prefix, 'FG-TIDA-WG0'}) if prefix}
                target = sequences['FG-TIDA-WG0']
                maximum = Contribution.objects.filter(number_prefix=target.prefix).order_by('-sequence_number').values_list('sequence_number', flat=True).first() or 0
                target.last_number = maximum + 1
                Contribution.objects.filter(pk=item.pk).update(group=wg0, number_prefix=target.prefix, sequence_number=target.last_number)
                target.save(update_fields=['last_number'])
                if old_prefix and old_prefix != target.prefix:
                    sequences[old_prefix].reorder()
                stats['contribution_routes_corrected'] += 1
            doc.group = wg0
            assign_source_type(doc, 'use-cases')
    for doc in core.documents(include_uncatalogued=True).select_related('tida_attribution').iterator(chunk_size=100):
        credits = source_attribution(safe_document_text(doc) or '')
        sources = list(doc.github_sources.filter(sync_mode='automatic'))
        for source in sources:
            for field in CREDIT_FIELDS:
                if not credits.get(field) and source.source_metadata.get(field):
                    credits[field] = clean_values(source.source_metadata[field], field)
        native = list(doc.documentauthor_set.select_related('person'))
        if not credits.get('affiliations'):
            credits['affiliations'] = list(dict.fromkeys(author.affiliation for author in native if author.affiliation))
        meta = getattr(doc, 'tida_attribution', None) or DocumentAttribution(document=doc)
        changed = []
        provenance = dict(meta.credit_provenance or {})
        imported = legacy_import_fields(meta, sources, doc.tida_versions.values_list('source_metadata', flat=True))
        for field in CREDIT_FIELDS:
            current = getattr(meta, field).strip()
            mode = 'source' if field in imported else provenance.get(field, {}).get('mode')
            if mode == 'profiles':
                continue
            cleaned = '\n'.join(clean_values(current, field))
            malformed = bool(current and cleaned != current)
            if mode == 'manual':
                if malformed:
                    setattr(meta, field, cleaned)
                    changed.append(field)
                continue  # Normalize formatting only; never replace editor facts.
            if current and not malformed and mode != 'source' and current.casefold() not in {'not entered', 'not provided', 'see submission contact below', 'tbd', 'tbc', 'unknown'}:
                continue
            if field == 'authors' and native and not malformed and not credits.get('authors'):
                continue
            replacement = '\n'.join(clean_values(credits.get(field, []), field)) or cleaned
            if replacement != current or (replacement and (mode != 'source' or field in imported)):
                setattr(meta, field, replacement)
                provenance[field] = {'mode': 'source', 'value': replacement}
                changed.append(field)
        if changed:
            stats['documents_with_metadata_completed'] += 1
            stats['metadata_fields_completed'] += len(changed)
            if apply:
                meta.credit_provenance = provenance
                meta.save()
        if not meta.authors and not native and not credits.get('authors'):
            stats['documents_without_explicit_author_evidence'] += 1
        if not meta.affiliations and not credits.get('affiliations'):
            stats['documents_without_explicit_affiliation_evidence'] += 1
    return stats
