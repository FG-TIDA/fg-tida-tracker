"""Resolve public credits consistently, retaining deliberate editorial choices."""
import re
from .github_attribution import CREDIT_FIELDS, plain_value, source_attribution, document_attribution


def clean_values(values, field):
    if isinstance(values, str):
        values = values.splitlines()
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for value in values:
        if not isinstance(value, str):
            continue
        if field == 'contact_emails':
            from .github_attribution import EMAIL
            candidates = EMAIL.findall(value)
        elif field in ('authors', 'contacts') and (re.match(r'(?i)^\s*(?:\*\*)?Name(?:\*\*)?\s*:', value) or
                (re.search(r'(?i)\bName\s*:', value) and re.search(r'(?i)\bOrgani[sz]ation\s*:', value))):
            candidates = document_attribution('Author: ' + value).get('authors', [])
        else:
            candidates = [plain_value(value)]
        for candidate in candidates:
            if candidate and candidate.casefold() not in {item.casefold() for item in result}:
                result.append(candidate)
    return result


def legacy_import_fields(override, sources, versions):
    """Recognize pre-provenance auto-fill only when recorded imports prove it."""
    automatic = [source for source in sources if source.sync_mode == 'automatic']
    if not override or not automatic:
        return set()
    provenance = override.credit_provenance or {}
    evidence = [source.source_metadata for source in automatic] + list(versions)
    return {key for key in CREDIT_FIELDS if key not in provenance and
        (entered := clean_values(getattr(override, key, ''), key)) and
        any(clean_values(record.get(key, []), key) == entered for record in evidence)}


def resolve_credits(body, override, version_metadata, sources, legacy_imports=()):
    credits = {field: clean_values(values, field) for field, values in source_attribution(body).items()}
    modes = getattr(override, 'credit_provenance', {}) or {}
    authoritative = set()
    profiles = modes.get('authors', {}).get('mode') == 'profiles'
    for key in CREDIT_FIELDS:
        entered = clean_values(getattr(override, key, ''), key)
        mode = 'source' if key in legacy_imports else modes.get(key, {}).get('mode')
        if mode == 'manual' or (entered and mode not in ('source', 'profiles')):
            credits[key] = entered
            authoritative.add(key)
        elif mode == 'profiles':
            credits[key] = []
        elif entered and mode == 'source' and not credits.get(key):
            credits[key] = entered
        if key not in authoritative and mode != 'profiles' and not credits.get(key):
            credits[key] = clean_values(version_metadata.get(key, []), key)
            if not credits[key]:
                credits[key] = clean_values([value for source in sources
                    for value in clean_values(source.source_metadata.get(key, []), key)], key)
    return credits, authoritative, profiles


def record_editor_changes(metadata, changed_fields):
    provenance = dict(metadata.credit_provenance or {})
    for field in set(changed_fields) & set(CREDIT_FIELDS):
        provenance[field] = {'mode': 'manual'}
    metadata.credit_provenance = provenance
