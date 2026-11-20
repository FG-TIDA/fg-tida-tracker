"""Link declared authors to explicit profiles, never to namesake accounts."""
from ietf.person.models import Person
from .credits import clean_values


def person_label(person):
    return ', '.join(clean_values(person.name, 'authors')) or 'Contributor'


def public_person_scope():
    from django.db.models import Q
    from . import core
    from .models import Contribution
    return (Q(role__group__in=core.groups()) | Q(documentauthor__document__in=core.documents()) |
        Q(user_id__in=Contribution.objects.filter(document__in=core.documents()).exclude(submitter_id=None).values('submitter_id')))


def document_people(doc, authors, credits, assigned_authors=None):
    submitted_ids = doc.contribution_set.exclude(submitter_id=None).values_list('submitter_id', flat=True)
    submitters = list(Person.objects.filter(user_id__in=submitted_ids).select_related('user').distinct().order_by('pk'))
    candidates = {person_label(item.person).casefold(): item.person for item in (assigned_authors if assigned_authors is not None else authors)}
    # Only actual submitters or explicitly assigned author profiles are eligible.
    # We do not search all registered accounts by a source author's name/email.
    for person in submitters:
        candidates.setdefault(person_label(person).casefold(), person)
        names = clean_values(credits.get('authors', []), 'authors')
        addresses = {value.casefold() for value in clean_values(credits.get('contact_emails', []), 'contact_emails')}
        if len(names) == 1 and person.user.email and person.user.email.casefold() in addresses:
            candidates.setdefault(names[0].casefold(), person)
    if authors:
        entries = [{'name': person_label(item.person), 'person_id': item.person_id,
                    'affiliation': item.affiliation} for item in authors]
    else:
        entries = [{'name': name, 'person_id': candidates[name.casefold()].pk if name.casefold() in candidates else None}
                   for name in clean_values(credits.get('authors', []), 'authors')]
    return entries, [{'name': person_label(person), 'person_id': person.pk} for person in submitters]
