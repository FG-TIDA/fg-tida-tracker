"""Public registration fields and aggregate meeting statistics."""
from collections import Counter
from django.core.paginator import Paginator
from django.db.models import Case, CharField, F, Q, Value, When
from django.db.models.functions import Concat, Lower, Trim
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_safe
from . import core


@require_safe
def participant_list(request, num):
    meeting = get_object_or_404(core.meetings(), number=num)
    public = meeting.tida_participants.filter(published=True)
    statistics = list(public.values('affiliation', 'country', 'attendance'))
    countries = Counter(row['country'].strip() for row in statistics if row['country'].strip())
    organizations = Counter(row['affiliation'].strip() for row in statistics if row['affiliation'].strip())
    attendance_counts = Counter(row['attendance'] for row in statistics)
    query = request.GET.get('q', '').strip()[:200]
    attendance = request.GET.get('attendance', '')
    attendance = 'remote' if attendance == 'online' else attendance
    if attendance not in ('onsite', 'remote', 'unknown'):
        attendance = ''
    records = public.annotate(public_name=Case(When(full_name='', then=Trim(Concat('first_name', Value(' '), 'last_name'))),
        default=F('full_name'), output_field=CharField()))
    if query:
        records = records.filter(Q(public_name__icontains=query) | Q(affiliation__icontains=query) | Q(country__icontains=query))
    if attendance:
        records = records.filter(attendance='' if attendance == 'unknown' else attendance)
    selected_sort = request.GET.get('sort', 'name')
    sorts = {'name': 'public_name', 'organization': 'affiliation', 'country': 'country', 'type': 'attendance'}
    if selected_sort not in sorts:
        selected_sort = 'name'
    records = records.order_by(Lower(sorts[selected_sort]), Lower('public_name'), 'pk')
    page = Paginator(records, 100).get_page(request.GET.get('page'))

    def url(**changes):
        params = request.GET.copy()
        params.pop('page', None)
        params.update(changes)
        return '?' + params.urlencode()

    return render(request, 'fgtida/participants.html', {'meeting': meeting, 'section': 'participants',
        'participants': page, 'page_obj': page, 'query': query, 'selected_attendance': attendance,
        'sort_links': [(key, label, url(sort=key)) for key, label in [('name', 'Full Name'),
            ('organization', 'Organization'), ('country', 'Country'), ('type', 'Reg Type')]],
        'previous_url': url(page=page.previous_page_number()) if page.has_previous() else '',
        'next_url': url(page=page.next_page_number()) if page.has_next() else '',
        'total': len(statistics), 'country_count': len(countries), 'organization_count': len({key.casefold() for key in organizations}),
        'countries': sorted(countries.items(), key=lambda pair: (-pair[1], pair[0])),
        'organizations': sorted(organizations.items(), key=lambda pair: (-pair[1], pair[0])),
        'onsite': attendance_counts['onsite'], 'online': attendance_counts['remote'], 'unknown': attendance_counts[''],
        'attendance_known': bool(attendance_counts['onsite'] or attendance_counts['remote'])})
