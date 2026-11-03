from pathlib import Path
from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.core.paginator import Paginator
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from ietf.doc.models import Document, DocumentAuthor, State
from . import core
from .files import extract_text, MAX_BYTES
from .models import Contribution


class ContributionForm(forms.ModelForm):
    upload = forms.FileField(help_text='PDF, DOCX, PPTX, TXT, Markdown or XML; maximum 10 MB.')

    class Meta:
        model = Contribution
        fields = ['title', 'group', 'abstract']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.db.models import Q
        self.fields['group'].queryset = core.groups().filter(Q(type_id='wg') | Q(acronym='incubation')).order_by('acronym')
        self.fields['group'].label = 'Working group or Incubation'
        self.fields['group'].help_text = 'Choose the relevant WG, or Incubation if your proposal requires a new working group.'

    def clean_upload(self):
        upload = self.cleaned_data['upload']
        self.content = upload.read(MAX_BYTES + 1)
        extract_text(self.content, upload.name)
        return upload


@login_required
def submit(request):
    form = ContributionForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        # Serialize a user's submissions to enforce a bounded pending queue.
        with transaction.atomic():
            from django.contrib.auth import get_user_model
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if Contribution.objects.filter(submitter=request.user, status='pending').count() >= 20:
                form.add_error(None, 'You already have 20 contributions awaiting review. Contact the secretariat.')
            else:
                item = form.save(commit=False)
                item.submitter = request.user
                item.content = form.content
                item.notifications_queued = False
                item.filename = Path(form.cleaned_data['upload'].name).name
                item.save()
                queue_notifications(item.pk)
                return redirect('fgtida:contribution', pk=item.pk)
    page = Paginator(Contribution.objects.filter(submitter=request.user).defer('content'), 25).get_page(request.GET.get('page'))
    return render(request, 'fgtida/submit.html', {'form': form, 'submissions': page, 'page': page, 'queryargs': ''})


@transaction.atomic
def queue_notifications(pk):
    """Leave submissions pending when recipients are not configured; retry safely later."""
    from .mail import enqueue
    item = Contribution.objects.select_for_update().get(pk=pk)
    if item.notifications_queued:
        return
    recipients = list(item.group.contribution_recipients.filter(active=True).values_list('email', flat=True))
    if not recipients:
        return
    for email in sorted(set(address.lower() for address in recipients)):
        enqueue(email, f'FG-TIDA contribution #{item.pk}: {item.title}',
            f'A new contribution has been submitted to {item.group.name}.\n'
            f'Title: {item.title}\n{item.abstract}\n'
            'The original submitted document is attached. It is awaiting editorial review.', contribution=item)
    item.notifications_queued = True
    item.save(update_fields=['notifications_queued'])


@login_required
def detail(request, pk, download=False):
    items = Contribution.objects.all()
    if not request.user.has_perm('fgtida.view_contribution'):
        items = items.filter(submitter=request.user)
    item = get_object_or_404(items, pk=pk)
    if download:
        from django.utils.http import content_disposition_header
        response = HttpResponse(bytes(item.content), content_type='application/octet-stream')
        response['Content-Disposition'] = content_disposition_header(True, item.filename)
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-store'
        return response
    return render(request, 'fgtida/contribution.html', {'submission': item})


@transaction.atomic
def accept(pk, user):
    item = Contribution.objects.select_for_update().get(pk=pk)
    if item.status != 'pending':
        return
    doc = Document(name=f'fg-tida-contribution-{item.pk}', title=item.title,
                   abstract=item.abstract, type_id='tida', group=item.group)
    core.save_document(doc, core.actor_for(user), f'Contribution {item.pk} accepted after review.',
                       data=bytes(item.content), filename=item.filename,
                       state=State.objects.filter(type_id='tida', slug='submitted').first())
    DocumentAuthor.objects.create(document=doc, person=core.actor_for(item.submitter), order=1)
    item.document = doc
    item.status = 'accepted'
    item.reviewed_by = user
    item.reviewed_at = timezone.now()
    item.save()
