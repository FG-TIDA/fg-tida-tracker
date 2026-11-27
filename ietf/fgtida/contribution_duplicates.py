"""Conservative candidates; never merge merely because authors or titles match."""
import re
import unicodedata
from django.db.models import Q
from django.db import connection
from .models import Contribution


def title_key(value):
    value = re.sub(r'^\s*\[use case\]\s*', '', value, flags=re.I)
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', value).casefold()))


def candidates(title, group, *, user=None, contact='', manifest=None):
    from .contribution_access import owned
    if connection.in_atomic_block:
        # Both public and admin creation paths hold this until the new record
        # commits, so simultaneous submissions in a group cannot pass together.
        from ietf.group.models import Group
        Group.objects.select_for_update().only('pk').get(pk=group.pk)
    records = Contribution.objects.filter(duplicate_of__isnull=True).defer('content', 'source_archive').select_related('document')
    digest = (manifest or {}).get('original_sha256')
    exact = records.filter(source_manifest__original_sha256=digest) if digest else records.none()
    visibility = Q(pk__in=owned(user).values('pk'))
    # Contact is supplied only by the administrator form, never a public form.
    if contact:
        visibility |= Q(contact_email__iexact=contact.strip())
    visible = records.filter(visibility).values('pk')
    key = title_key(title)
    matches = list(exact.filter(Q(pk__in=visible) | Q(document__in=core_documents())))
    for item in records.filter(pk__in=visible, group=group):
        other = title_key(item.title)
        if other == key or (min(len(key), len(other)) >= 40 and (key.startswith(other + ' ') or other.startswith(key + ' '))):
            if item.pk not in {match.pk for match in matches}:
                matches.append(item)
    return matches


def core_documents():
    from .core import documents
    return documents()
