"""One public liaison catalogue backed by existing document/listing records."""
from django import forms
from django.core.paginator import Paginator
from django.db.models import DateField, Q
from django.db.models.functions import Cast, Coalesce
from django.shortcuts import get_object_or_404, render
from . import core
from .models import DocumentListing


class LiaisonSearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=200, label='Search',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Title, body, identifiers, etc.'}))
    source = forms.CharField(required=False, max_length=255, label='Source', widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'From group(s) or organization(s)'}))
    destination = forms.CharField(required=False, max_length=255, label='Destination', widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'To group(s) or organization(s)'}))
    start_date = forms.DateField(required=False, label='Start date', widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    end_date = forms.DateField(required=False, label='End date', widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    direction = forms.ChoiceField(required=False, choices=[('', 'Inputs and outputs'), ('input', 'Input'), ('output', 'Output')],
        widget=forms.Select(attrs={'class': 'form-select'}))

    def clean(self):
        values = super().clean()
        if values.get('start_date') and values.get('end_date') and values['start_date'] > values['end_date']:
            self.add_error('end_date', 'End date must be on or after start date.')
        return values


def public_listings():
    return DocumentListing.objects.filter(document__in=core.documents()).select_related('document', 'meeting').annotate(
        statement_date=Coalesce('posted_date', Cast('created', DateField())))


def index(request):
    form = LiaisonSearchForm(request.GET)
    records = public_listings()
    if form.is_valid():
        values = form.cleaned_data
        if query := values.get('q'):
            records = records.filter(Q(document__title__icontains=query) | Q(document__name__icontains=query)
                | Q(document__abstract__icontains=query) | Q(body__icontains=query) | Q(purpose__icontains=query))
        if source := values.get('source'):
            records = records.filter(source__icontains=source)
        if destination := values.get('destination'):
            records = records.filter(destination__icontains=destination)
        if values.get('start_date'):
            records = records.filter(statement_date__gte=values['start_date'])
        if values.get('end_date'):
            records = records.filter(statement_date__lte=values['end_date'])
        if values.get('direction'):
            records = records.filter(direction=values['direction'])
    else:
        records = records.none()
    sort = request.GET.get('sort', '-date')
    allowed = {'date': 'statement_date', 'from': 'source', 'to': 'destination', 'deadline': 'deadline', 'title': 'document__title'}
    key = sort.removeprefix('-')
    if key not in allowed:
        sort, key = '-date', 'date'
    records = records.order_by(('-' if sort.startswith('-') else '') + allowed[key], '-pk')
    page = Paginator(records, 50).get_page(request.GET.get('page'))

    def url(**changes):
        params = request.GET.copy()
        params.pop('page', None)
        params.update(changes)
        return '?' + params.urlencode()

    return render(request, 'fgtida/liaisons.html', {'form': form, 'page': page,
        'headers': [(label, url(sort=('-' + field) if sort == field else field)) for field, label in
                    [('date', 'Date'), ('from', 'From'), ('to', 'To'), ('deadline', 'Deadline'), ('title', 'Title')]],
        'previous_url': url(page=page.previous_page_number()) if page.has_previous() else '',
        'next_url': url(page=page.next_page_number()) if page.has_next() else ''})


def detail(request, pk):
    listing = get_object_or_404(public_listings(), pk=pk)
    from .views import document
    return document(request, name=listing.document.name)
