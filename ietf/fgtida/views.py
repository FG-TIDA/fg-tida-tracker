from datetime import timedelta
from pathlib import Path
from difflib import unified_diff
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.core.paginator import Paginator
from django.db.models import Q, F, Value, DateField, Prefetch, Min, Max, Exists, OuterRef
from django.db.models.functions import Cast
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from ietf.doc.models import DocumentAuthor, State, RelatedDocument
from ietf.meeting.models import Session, SchedTimeSessAssignment, ImportantDate, FloorPlan
from ietf.group.models import Role
from ietf.person.models import Person
from . import core
from . import workflow


def floor_plan(request, num, pk):
    plan = get_object_or_404(FloorPlan, pk=pk, meeting__number=num, meeting__type_id='tida')
    if not plan.image or not plan.image.storage.exists(plan.image.name):
        raise Http404
    response = FileResponse(plan.image.open('rb'), filename=Path(plan.image.name).name)
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def home(request):
    return render(request, 'fgtida/home.html', {'types': core.TYPE_LABELS,
        'groups': core.groups(), 'categories': core.CATEGORIES})


def database_access(request):
    from django.conf import settings
    if not settings.DEBUG:
        raise Http404
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    return HttpResponse(status=204 if request.user.is_active and request.user.is_superuser else 403)


def incubation(request):
    return search(request, acronym='incubation', incubation=True)


def search(request, acronym=None, incubation=False, category_slug=None):
    query = (request.GET.get('name') or request.GET.get('q') or '').strip()[:200]
    docs = core.documents().select_related('group', 'group__tida_configuration', 'type', 'tida_listing', 'tida_listing__meeting').prefetch_related('states', 'tags', 'tida_versions').annotate(
        first_activity=Min('docevent__time'), latest_revision=Max('docevent__time', filter=Q(docevent__type='new_revision')))
    if query:
        docs = docs.filter(Q(name__icontains=query) | Q(title__icontains=query) | Q(abstract__icontains=query))
    group = acronym or request.GET.get('group', '')
    kind = request.GET.get('type', '')
    category = category_slug or request.GET.get('category', '')
    collection = request.GET.get('collection', '')
    collection = collection if collection in {'input', 'output'} else ''
    if collection:
        docs = docs.filter(tida_listing__direction=collection)
    author = request.GET.get('author', '').strip()[:200]
    state = request.GET.get('state', '')
    if incubation:
        from .models import Contribution
        docs = docs.filter(Q(group__acronym='incubation') | Q(pk__in=Contribution.objects.filter(group__acronym='incubation').values('document_id')))
    elif group:
        docs = docs.filter(group__acronym=group)
    if kind:
        docs = docs.filter(type_id=kind)
    if category:
        docs = docs.filter(tags__slug='tida-' + category)
    if author:
        docs = docs.filter(pk__in=DocumentAuthor.objects.filter(Q(person__name__icontains=author) | Q(affiliation__icontains=author)).values('document_id'))
    if state:
        docs = docs.filter(states__slug=state)
    recent = request.GET.get('recent', '')
    cutoff = timezone.now() - timedelta(days=workflow.RECENT_DAYS)
    if recent == 'new':
        docs = docs.filter(first_activity__gte=cutoff, first_activity__lte=timezone.now())
    elif recent == 'updated':
        docs = docs.filter(first_activity__lt=cutoff, latest_revision__gte=cutoff, latest_revision__lte=timezone.now())
    sort = request.GET.get('sort', '-time')
    if sort not in ('-time', 'time', 'name', 'title'):
        sort = '-time'
    ordering = '-tida_listing__created' if collection and 'sort' not in request.GET else sort
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'fgtida/search.html', {
        'page': Paginator(docs.distinct().order_by(ordering, '-pk'), 25).get_page(request.GET.get('page')),
        'collection': collection, 'collection_title': {'input': 'Liaison statements inputs', 'output': 'Liaison statements outputs'}.get(collection, ''),
        'group_collection_title': dict(core.CATEGORIES).get(category_slug, ''),
        'recent': recent, 'sort': sort,
        'query': query, 'selected_group': group, 'selected_type': kind, 'selected_category': category,
        'author': author, 'selected_state': state, 'groups': core.groups().order_by('name'),
        'types': core.TYPE_LABELS,
        'states': State.objects.filter(type_id__in=core.DOCUMENT_TYPES).values('slug', 'name').distinct().order_by('name'),
        'categories': core.CATEGORIES, 'queryargs': params.urlencode(),
        'incubation': incubation,
        'group_obj': get_object_or_404(core.groups(), acronym=acronym) if acronym and not incubation else None,
        'section': category_slug or 'documents',
    })


def document(request, name, rev=None):
    doc = get_object_or_404(core.documents().select_related('group', 'type').prefetch_related('states', 'tags'), name=name)
    from .versioning import archive_revision, labels
    rev=archive_revision(doc,rev)
    snapshot = doc
    if rev and rev != doc.rev:
        snapshot = doc.history_set.filter(rev=rev).order_by('-time', '-id').first()
        if snapshot is None:
            raise Http404
    revisions = doc.history_set.exclude(rev='').exclude(uploaded_filename='').exclude(uploaded_filename__isnull=True).order_by('-time', '-id')
    unique = {}
    for item in revisions:
        unique.setdefault(item.rev, item)
    from .models import WorkItem
    work_item=WorkItem.objects.select_related('repository').defer('working_pdf').filter(document=doc).first()
    releases={item.revision:item for item in work_item.releases.defer('source_archive')} if work_item else {}
    for item in unique.values():
        item.github_release=releases.get(item.rev)
    version_rows={row.revision:row for row in doc.tida_versions.all()}
    ordered=sorted(unique.values(),key=lambda row:int(row.rev))
    for item in ordered:
        record=version_rows.get(item.rev)
        item.public_version=record.version if record else item.rev
        item.recorded=record.recorded if record else item.time
    from datetime import timedelta
    end=max(timezone.now(),ordered[-1].recorded+timedelta(days=1)) if ordered else timezone.now()
    for index,item in enumerate(ordered):
        until=ordered[index+1].recorded if index+1<len(ordered) else end
        item.timeline_days=max(1,(until-item.recorded).total_seconds()/86400)
    snapshot._version_labels=labels(doc)
    abstract=snapshot.abstract
    if abstract.lstrip().startswith('# ') and doc.github_sources.filter(sync_mode='automatic').exists():
        # Legacy imports used a truncated source prefix, not an actual abstract.
        # Keep the archived source intact and let editors supply a real abstract.
        abstract=''
    file_available = bool(snapshot.uploaded_filename) and Path(snapshot.get_file_name()).is_file()
    return render(request, 'fgtida/document.html', {'doc': doc, 'snapshot': snapshot,
        'revisions': ordered, 'file_available': file_available,'display_abstract':abstract,
        'version_records':version_rows,'selected_version':version_rows.get(snapshot.rev),'selected_release':releases.get(snapshot.rev),
        'last_change':doc.docevent_set.order_by('-time','-pk').first(),
        'text': snapshot.text() if file_available else None,
        'text_is_markdown': Path(snapshot.uploaded_filename or '').suffix.lower() in ('.md', '.markdown'),
        'text_is_rst': Path(snapshot.uploaded_filename or '').suffix.lower() == '.rst' or (Path(snapshot.uploaded_filename or '').suffix.lower() == '.zip' and not hasattr(doc, 'tida_listing')),
        'text_is_package': Path(snapshot.uploaded_filename or '').suffix.lower() == '.zip' and not hasattr(doc, 'tida_listing'),
        'work_item':work_item,'old_revision':bool(rev and rev!=doc.rev),'show_working':not rev,
        'events': doc.docevent_set.select_related('by')[:100],
        'authors': doc.documentauthor_set.select_related('person', 'email').order_by('order'),
        'source_records': doc.github_sources.select_related('repository').all(),
        'sessions': doc.session_set.select_related('meeting'),
        'relationships': RelatedDocument.objects.filter(source=doc, target__in=core.documents()).select_related('target', 'relationship'),
        'incoming': RelatedDocument.objects.filter(target=doc, source__in=core.documents()).select_related('source', 'relationship'),
    })


def history(request, name):
    doc = get_object_or_404(core.documents(), name=name)
    return render(request, 'fgtida/history.html', {'doc': doc,
        'page': Paginator(doc.docevent_set.select_related('by').order_by('-time', '-pk'), 50).get_page(request.GET.get('page')),
        'revisions': doc.history_set.exclude(rev='').exclude(uploaded_filename='').exclude(uploaded_filename__isnull=True).values_list('rev', flat=True).order_by('rev').distinct()})


def download(request, name, rev):
    doc = get_object_or_404(core.documents(), name=name)
    from .versioning import archive_revision,public_version
    rev=archive_revision(doc,rev)
    snapshot = doc if doc.rev == rev else doc.history_set.filter(rev=rev).order_by('-time', '-id').first()
    if snapshot is None or not snapshot.uploaded_filename:
        raise Http404
    file = Path(snapshot.get_file_name())
    if not file.is_file():
        raise Http404
    response = FileResponse(file.open('rb'), as_attachment=True, filename=f'{doc.name}-{public_version(doc,rev)}{file.suffix}')
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def groups(request):
    records = core.groups().select_related('type', 'state', 'parent','tida_configuration').order_by('tida_configuration__number','name')
    records = records.filter(type_id='wg').exclude(state_id='conclude')
    state = ''
    return render(request, 'fgtida/groups.html', {'groups': records, 'selected_state': state})


def group(request, acronym, section='about'):
    group = get_object_or_404(core.groups().select_related('state', 'type'), acronym=acronym)
    context = {'group': group, 'group_obj': group, 'section': section,
        'roles': group.role_set.select_related('person', 'name').order_by('name__name', 'person__name'),
        'documents': core.documents().filter(group=group).select_related('type').order_by('-time')[:10],
        'milestones': group.groupmilestone_set.exclude(state_id='deleted').prefetch_related('docs').order_by('due'),
        'resources': group.groupurl_set.all()}
    if section == 'charter':
        context['charter'] = core.documents().filter(group=group, type_id='charter').order_by('-time').first()
        if group.charter_id:
            context['charter'] = core.documents().filter(pk=group.charter_id, group=group).first() or context['charter']
    if section in ('members', 'photos'):
        people = Person.objects.filter(role__group=group).distinct().order_by('name', 'pk').prefetch_related(
            Prefetch('role_set', queryset=Role.objects.filter(group=group).select_related('name'), to_attr='fg_roles'))
        query = request.GET.get('q', '').strip()[:200]
        if query:
            people = people.filter(name__icontains=query)
        context.update(page=Paginator(people, 24).get_page(request.GET.get('page')), query=query,
                       queryargs=urlencode({'q': query}))
    elif section == 'history':
        context['page'] = Paginator(group.groupevent_set.select_related('by').order_by('-time', '-pk'), 50).get_page(request.GET.get('page'))
    elif section not in ('about', 'charter', 'milestones'):
        raise Http404
    return render(request, 'fgtida/group.html', context)


def meetings(request, scope='upcoming', acronym=None):
    records = core.meetings().select_related('category').annotate(end=Cast(
        F('date') + (F('days') - 1) * Value(timedelta(days=1)), output_field=DateField()))
    today = timezone.localdate()
    if scope == 'past':
        records = records.filter(end__lt=today).order_by('-date', '-pk')
    elif scope == 'upcoming':
        records = records.filter(end__gte=today).order_by('date', 'pk')
    else:
        raise Http404
    group_obj = get_object_or_404(core.groups(), acronym=acronym) if acronym else None
    group_slug = acronym or request.GET.get('group', '')
    if group_slug:
        records = records.filter(session__group__acronym=group_slug).distinct()
    query = request.GET.get('q', '').strip()[:200]
    if query:
        records = records.filter(Q(title__icontains=query) | Q(number__icontains=query) | Q(city__icontains=query) | Q(session__name__icontains=query)).distinct()
    year = request.GET.get('year', '')
    status = request.GET.get('status', '')
    if status in workflow.SESSION_STATES:
        records = records.filter(Exists(Session.objects.with_current_status().filter(meeting_id=OuterRef('pk'), current_status=status)))
    if year.isdigit() and 1900 <= int(year) <= 9999:
        records = records.filter(date__year=int(year))
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'fgtida/meetings.html', {'scope': scope,
        'page': Paginator(records.prefetch_related('session_set', Prefetch('session_set',
            queryset=Session.objects.with_current_status(), to_attr='workflow_sessions')), 25).get_page(request.GET.get('page')),
        'selected_status': status, 'session_states': [(slug, label) for slug, (label, color) in workflow.SESSION_STATES.items()],
        'group_obj': group_obj, 'section': 'meetings', 'groups': core.groups(), 'selected_group': group_slug,
        'query': query, 'year': year, 'queryargs': params.urlencode()})


def registration(request):
    from .content import get_content
    return redirect(get_content(request).registration_url)


def preferences(request):
    return render(request, 'fgtida/preferences.html')


def important_dates(request):
    dates = ImportantDate.objects.filter(name__slug__startswith='tida-').select_related('name').order_by('date', 'pk')
    records = core.meetings().filter(importantdate__in=dates).distinct().order_by('-date', '-pk').prefetch_related(
        Prefetch('importantdate_set', queryset=dates, to_attr='published_dates'))
    return render(request, 'fgtida/important_dates.html', {
        'page': Paginator(records, 10).get_page(request.GET.get('page'))})


def current_agenda(request, section='agenda'):
    today = timezone.localdate()
    recent = core.meetings().filter(date__lte=today).order_by('-date').first()
    selected = recent if recent and recent.date + timedelta(days=max(recent.days, 1)-1) >= today else core.meetings().filter(date__gte=today).order_by('date').first()
    selected = selected or recent
    if not selected:
        return render(request, 'fgtida/account_form.html', {'title': 'Meeting agenda', 'message': 'No meetings have been announced yet.'})
    if section == 'floor-plan':
        plans = FloorPlan.objects.filter(meeting=selected).order_by('pk')
        return render(request, 'fgtida/floor_plans.html', {'meeting': selected, 'plans': plans, 'section': 'floor-plan'})
    return redirect('fgtida:meeting', num=selected.number)


def meeting(request, num, section='agenda'):
    selected_meeting = request.GET.get('meeting')
    if selected_meeting and selected_meeting != num:
        target = get_object_or_404(core.meetings(), number=selected_meeting)
        return redirect(reverse('fgtida:meeting', args=[target.number]) + '?' + urlencode({k: v for k, v in request.GET.items() if k not in {'meeting', 'day', 'month'}}))
    meeting = get_object_or_404(core.meetings().select_related('category'), number=num)
    sessions = Session.objects.with_current_status().filter(meeting=meeting).select_related('group').order_by('pk').prefetch_related(
        Prefetch('materials', queryset=core.documents().select_related('type').order_by('type_id', 'title'), to_attr='public_materials'))
    if section == 'agenda':
        zone_mode = request.GET.get('tz', 'meeting')
        if zone_mode not in ('meeting', 'UTC', 'local'):
            zone_mode = 'meeting'
        zone = 'UTC' if zone_mode == 'UTC' else (meeting.time_zone or 'UTC')
        local_zone = request.GET.get('local_zone', '')[:100]
        if zone_mode == 'local' and local_zone:
            try:
                ZoneInfo(local_zone)
                zone = local_zone
            except (ZoneInfoNotFoundError, ValueError):
                local_zone = ''
        query = request.GET.get('q', '').strip()[:200]
        selected_group = request.GET.get('group', '')
        if query:
            sessions = sessions.filter(Q(name__icontains=query) | Q(group__name__icontains=query) | Q(agenda_note__icontains=query))
        if selected_group:
            sessions = sessions.filter(group__acronym=selected_group)
        assignments = list(SchedTimeSessAssignment.objects.filter(schedule_id=meeting.schedule_id,
            session__in=sessions, timeslot__meeting=meeting).select_related(
            'timeslot', 'timeslot__location', 'session', 'session__group').prefetch_related(
            Prefetch('session__materials', queryset=core.documents().select_related('type'), to_attr='public_materials'), 'session__tida_resources'
            ).order_by('timeslot__time', 'timeslot__location__name', 'pk'))
        days = {}
        status_by_session = dict(sessions.values_list('pk', 'current_status'))
        for assignment in assignments:
            assignment.session.current_status = status_by_session[assignment.session_id]
            start = timezone.localtime(assignment.timeslot.time, ZoneInfo(zone))
            assignment.ends_at = assignment.timeslot.time + assignment.timeslot.duration
            days.setdefault(start.date(), []).append(assignment)
        import calendar as month_calendar
        from datetime import datetime
        try:
            calendar_month = datetime.strptime(request.GET.get('month', ''), '%Y-%m').date()
            if not 1900 <= calendar_month.year <= 2200:
                raise ValueError('Unsupported calendar year')
        except ValueError:
            calendar_month = meeting.date.replace(day=1)
        selected_day = request.GET.get('day', '')
        all_days = list(days.items())
        shown_days = [(day, entries) for day, entries in all_days if not selected_day or day.isoformat() == selected_day]
        base_params = {k: v for k, v in request.GET.items() if k not in {'day', 'month', 'meeting'}}
        def calendar_link(**extra):
            return '?' + urlencode({**base_params, **extra})
        calendar_rows = [[{'date': day, 'active': day in days, 'selected': day.isoformat() == selected_day,
            'outside': day.month != calendar_month.month,
            'url': calendar_link(day=day.isoformat(), month=day.strftime('%Y-%m'))}
            for day in week] for week in month_calendar.Calendar().monthdatescalendar(calendar_month.year, calendar_month.month)]
        previous_month = calendar_month-timedelta(days=1)
        next_month = (calendar_month+timedelta(days=32)).replace(day=1)
        return render(request, 'fgtida/agenda.html', {'meeting': meeting, 'section': section,
            'display_zone': zone, 'zone_mode': zone_mode, 'local_zone': local_zone,
            'days': shown_days, 'all_days': all_days, 'query': query,
            'meeting_choices': core.meetings().order_by('-date').only('number', 'title', 'date', 'city'),
            'floor_plans': FloorPlan.objects.filter(meeting=meeting).order_by('order', 'pk'),
            'calendar_rows': calendar_rows, 'calendar_month': calendar_month,
            'previous_month_url': calendar_link(month=previous_month.strftime('%Y-%m')),
            'next_month_url': calendar_link(month=next_month.strftime('%Y-%m')),
            'all_days_url': calendar_link(),
            'groups': core.groups(), 'selected_group': selected_group,
            'unscheduled': sessions.exclude(pk__in=[item.session_id for item in assignments]),
            'filtered': bool(query or selected_group or selected_day),
            'scheduling_history': meeting.session_set.values('schedulingevent__time', 'schedulingevent__status__name', 'name').exclude(schedulingevent__isnull=True).order_by('-schedulingevent__time')[:100]})
    if section == 'materials':
        from .models import SessionResource
        documents = core.documents().filter(session__meeting=meeting).select_related('type').distinct().order_by('type_id', 'title', 'pk')
        resources = SessionResource.objects.filter(session__meeting=meeting, session__group__in=core.groups()).select_related('session').order_by('kind', 'title', 'pk')
        return render(request, 'fgtida/meeting_materials.html', {
            'meeting': meeting,
            'page': Paginator(documents, 25).get_page(request.GET.get('page')),
            'resources_page': Paginator(resources, 25).get_page(request.GET.get('resources_page')),
            'upload_session': sessions.first(),
            'resource_page_value': request.GET.get('resources_page', '1'),
        })
    page = Paginator(sessions, 25).get_page(request.GET.get('page'))
    assignments = SchedTimeSessAssignment.objects.filter(schedule_id=meeting.schedule_id, session__in=page.object_list).select_related('timeslot', 'session', 'timeslot__location').order_by('timeslot__time')
    zone = 'UTC' if request.GET.get('tz') == 'UTC' else meeting.time_zone
    return render(request, 'fgtida/meeting.html', {'meeting': meeting, 'sessions': page, 'page': page, 'assignments': assignments,
        'section': section, 'display_zone': zone, 'queryargs': 'tz=UTC' if zone == 'UTC' else '',
        'important_dates': ImportantDate.objects.filter(meeting=meeting).select_related('name').order_by('date')})


def snapshot_for(name, rev):
    doc = get_object_or_404(core.documents(), name=name)
    from .versioning import archive_revision
    rev=archive_revision(doc,rev)
    snapshot = doc if doc.rev == rev else doc.history_set.filter(rev=rev).order_by('-time', '-pk').first()
    if snapshot is None:
        raise Http404
    return snapshot


def document_text(request, name, rev):
    snapshot = snapshot_for(name, rev)
    path = Path(snapshot.get_file_name()).with_suffix('.txt')
    if not snapshot.uploaded_filename or not path.is_file():
        raise Http404
    response = FileResponse(path.open('rb'), content_type='text/plain; charset=utf-8', as_attachment=True, filename=f'{name}-{rev}.txt')
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def compare(request, name):
    doc = get_object_or_404(core.documents(), name=name)
    from .versioning import archive_revision,public_version
    revisions = list(doc.history_set.exclude(rev='').exclude(uploaded_filename='').exclude(uploaded_filename__isnull=True).values_list('rev', flat=True).order_by('rev').distinct())
    before = archive_revision(doc,request.GET.get('from', revisions[-2] if len(revisions) > 1 else doc.rev))
    after = archive_revision(doc,request.GET.get('to', doc.rev))
    texts = []
    for rev in (before, after):
        snapshot = snapshot_for(name, rev)
        path = Path(snapshot.get_file_name()).with_suffix('.txt')
        texts.append(path.read_text(encoding='utf-8', errors='replace') if snapshot.uploaded_filename and path.is_file() else None)
    diff = None
    # Bound the synchronous comparison to keep large files from monopolizing a worker.
    if all(text is not None and len(text) <= 100000 and len(text.splitlines()) <= 3000 for text in texts):
        diff = ''.join(unified_diff(texts[0].splitlines(True), texts[1].splitlines(True), fromfile=public_version(doc,before), tofile=public_version(doc,after)))
    return render(request, 'fgtida/compare.html', {'doc': doc, 'revisions': revisions, 'before': before, 'after': after, 'diff': diff})


def member_photo(request, person_id):
    scope = Q(role__group__in=core.groups()) | Q(documentauthor__document__in=core.documents())
    if request.user.is_authenticated:
        scope |= Q(user=request.user)
    person = get_object_or_404(Person.objects.filter(scope).distinct(), pk=person_id)
    photo = person.photo_thumb or person.photo
    if not photo or not photo.storage.exists(photo.name):
        raise Http404
    response = FileResponse(photo.open('rb'))
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def person(request, person_id):
    scope = Q(role__group__in=core.groups()) | Q(documentauthor__document__in=core.documents())
    if request.user.is_authenticated:
        scope |= Q(user=request.user)
    person = get_object_or_404(Person.objects.filter(scope).distinct(), pk=person_id)
    return render(request, 'fgtida/person.html', {'person': person,
        'roles': person.role_set.filter(group__in=core.groups()).select_related('group', 'name'),
        'documents': core.documents().filter(documentauthor__person=person).distinct().order_by('-time')[:25]})


def calendar(request, num=None, acronym=None):
    from icalendar import Calendar, Event
    records = core.meetings()
    if num:
        records = records.filter(pk=get_object_or_404(records, number=num).pk)
    if acronym:
        group = get_object_or_404(core.groups(), acronym=acronym)
        records = records.filter(session__group=group).distinct()
    result = Calendar()
    result.add('prodid', '-//FG-TIDA//Datatracker//EN')
    result.add('version', '2.0')
    for meeting in records.order_by('date').iterator():
        status_by_session = dict(Session.objects.with_current_status().filter(meeting=meeting).values_list('pk', 'current_status'))
        assignments = SchedTimeSessAssignment.objects.filter(schedule_id=meeting.schedule_id, session__isnull=False).select_related('session', 'timeslot', 'timeslot__location')
        if acronym:
            assignments = assignments.filter(session__group=group)
        scheduled = list(assignments)
        for assignment in scheduled or [None]:
            event = Event()
            event.add('uid', f'fg-tida-{meeting.pk}-{assignment.pk if assignment else "meeting"}@tracker')
            event.add('dtstamp', timezone.now())
            event.add('url', request.build_absolute_uri(reverse('fgtida:meeting', args=[meeting.number])))
            if assignment:
                status = status_by_session.get(assignment.session_id, '')
                event.add('status', 'CANCELLED' if status in workflow.CANCELLED else 'CONFIRMED' if status == 'sched' else 'TENTATIVE')
                event.add('summary', assignment.session.name or meeting.display_title)
                event.add('dtstart', assignment.timeslot.time)
                event.add('dtend', assignment.timeslot.time + assignment.timeslot.duration)
                event.add('location', str(assignment.timeslot.location or meeting.city))
            else:
                all_cancelled = bool(status_by_session) and set(status_by_session.values()) <= workflow.CANCELLED
                event.add('status', 'CANCELLED' if all_cancelled else 'TENTATIVE')
                event.add('summary', meeting.display_title)
                event.add('dtstart', meeting.date)
                event.add('dtend', meeting.date + timedelta(days=meeting.days))
                event.add('location', meeting.city)
            result.add_component(event)
    response = HttpResponse(result.to_ical(), content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="fg-tida-calendar.ics"'
    return response


def legacy(request, path=''):
    # Old catalog bookmarks remain useful, without keeping another backend active.
    if path.startswith('meetings'):
        return redirect('fgtida:upcoming')
    if path.startswith('groups'):
        return redirect('fgtida:groups')
    if path in ('dashboard/', 'documents/submit/'):
        return redirect('/admin/doc/document/')
    category = path.strip('/')
    if category in dict(core.CATEGORIES):
        return redirect(reverse('fgtida:documents') + '?category=' + category)
    return redirect(reverse('fgtida:documents') + ('?' + request.GET.urlencode() if request.GET else ''))
