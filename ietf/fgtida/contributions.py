from pathlib import Path
from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.core.paginator import Paginator
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from ietf.doc.models import Document, DocumentAuthor, State
from . import core
from .files import MAX_BYTES
from .models import Contribution, WorkItem, GitHubJob


class ContributionForm(forms.ModelForm):
    submission_method = forms.ChoiceField(choices=[('github', 'GitHub repository'), ('rst', 'RST source ZIP'),
        ('word', 'Word (.docx) — exceptional fallback')], label='How will you submit?')
    upload = forms.FileField(required=False, help_text='RST: upload your completed ZIP. Word fallback: upload .docx. Maximum 10 MB. Leave empty for GitHub.')
    publication_consent = forms.BooleanField(label='I understand that the confirmed contribution and its source files will be public.')

    class Meta:
        model = Contribution
        fields = ['title', 'group', 'abstract', 'work_item_acronym', 'submission_method',
                  'draft_repository_url', 'source_commit', 'source_folder', 'entry_document']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.db.models import Q
        self.fields['group'].queryset = core.groups().filter(Q(type_id='wg') | Q(acronym='incubation')).order_by('tida_configuration__number','name')
        from .templatetags.fgtida import group_label
        self.fields['group'].label_from_instance = group_label
        self.fields['group'].queryset = self.fields['group'].queryset.exclude(state_id='conclude')
        self.fields['group'].label = 'Working group or No working group yet'
        self.fields['group'].help_text = 'Choose an existing wg, or No working group yet for new work that does not belong to an existing wg. No working group yet is a submission route, not a working group.'
        self.fields['draft_repository_url'].help_text = 'GitHub only: your public repository in your own GitHub account, not the template URL.'
        self.fields['source_commit'].help_text = 'GitHub only: copy the full 40-character commit SHA of your completed contribution.'
        self.fields['source_folder'].help_text = 'GitHub only: documentation folder relative to the repository root, normally src/doc. All referenced assets must be inside it.'
        self.fields['entry_document'].help_text = 'GitHub: path from repository root, e.g. src/doc/index.rst. ZIP: full path inside the ZIP, e.g. my-contribution/src/doc/index.rst. Leave empty for Word.'
        self.fields['work_item_acronym'].help_text = 'Optional short identifier for the work item repository.'
        self.fields['upload'].help_text += ' Submitting creates a public work-item record and archives this file as version 0.0.'

    def clean_draft_repository_url(self):
        value = self.cleaned_data.get('draft_repository_url')
        if value:
            from .github_client import repository_name
            return 'https://github.com/'+repository_name(value)
        return ''

    def clean_upload(self):
        upload = self.cleaned_data.get('upload')
        if upload and upload.size > MAX_BYTES:
            raise forms.ValidationError('Upload a file of at most 10 MB.')
        return upload

    def clean(self):
        values = super().clean()
        if self.errors:
            return values
        from .source_packages import prepare_zip, github_snapshot, convert_word
        method = values['submission_method']
        upload = values.get('upload')
        try:
            if method == 'github':
                if upload or not all(values.get(key) for key in ('draft_repository_url', 'source_commit', 'source_folder', 'entry_document')):
                    raise forms.ValidationError('For GitHub, provide the repository URL, full commit SHA, source folder and entry document. Do not upload a file.')
                self.source_archive, self.manifest = github_snapshot(values['draft_repository_url'], values['source_commit'],
                    values['source_folder'], values['entry_document'])
                values['source_commit'] = self.manifest['commit']
                self.content = self.source_archive
                self.filename = 'github-source.zip'
            else:
                if not upload:
                    raise forms.ValidationError('Choose your completed ZIP or Word document to upload.')
                if any(values.get(key) for key in ('draft_repository_url', 'source_commit', 'source_folder')):
                    raise forms.ValidationError('Repository URL, commit and source folder are for the GitHub route only. Clear them for an upload.')
                self.content = upload.read(MAX_BYTES + 1)
                self.filename = Path(upload.name).name
                if method == 'rst':
                    if Path(upload.name).suffix.lower() != '.zip':
                        raise forms.ValidationError('Upload an RST source ZIP with all supporting files.')
                    entry = values.get('entry_document', '')
                    from pathlib import PurePosixPath
                    folder = str(PurePosixPath(entry).parent)
                    self.source_archive, self.manifest = prepare_zip(self.content, entry, '' if folder == '.' else folder)
                else:
                    if Path(upload.name).suffix.lower() != '.docx':
                        raise forms.ValidationError('The Word fallback requires a .docx file.')
                    self.source_archive, self.manifest = convert_word(self.content)
                    values['entry_document'] = 'index.rst'
            import hashlib
            self.manifest['original_sha256'] = hashlib.sha256(self.content).hexdigest()
            self.manifest['method'] = method
        except forms.ValidationError as error:
            self.add_error(None, error)
        return values


@login_required
def submit(request):
    selected = core.groups().filter(acronym=request.GET.get('group', ''), acronym__in=['incubation']).first()
    form = ContributionForm(request.POST or None, request.FILES or None, initial={'group': selected} if selected else None)
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
                item.filename = form.filename
                item.source_archive = form.source_archive
                item.source_manifest = form.manifest
                item.save()
                doc = Document(name=f'fg-tida-contribution-{item.pk}', title=item.title,
                    abstract=item.abstract, type_id='tida', group=item.group)
                core.save_document(doc, core.actor_for(request.user), 'Work item submitted for a group decision.',
                    data=bytes(item.source_archive),filename='source.zip',state=State.objects.get(type_id='tida',slug='submitted'))
                DocumentAuthor.objects.create(document=doc, person=core.actor_for(request.user), order=1)
                item.document=doc
                item.save(update_fields=['document'])
                from django.utils.text import slugify
                WorkItem.objects.create(document=doc,short_name=item.work_item_acronym or slugify(item.title)[:48] or f'item-{item.pk}')
                queue_notifications(item.pk)
                from .email_templates import contribution
                from .mail import enqueue
                if request.user.email:
                    subject, body=contribution(item,acknowledgement=True)
                    enqueue(request.user.email,subject,body,contribution=item,purpose='submitter')
                return redirect('fgtida:contribution', pk=item.pk)
    page = Paginator(Contribution.objects.filter(submitter=request.user).defer('content', 'source_archive'), 25).get_page(request.GET.get('page'))
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
        from .email_templates import contribution
        subject,body=contribution(item)
        enqueue(email,subject,body,contribution=item)
    item.notifications_queued = True
    item.save(update_fields=['notifications_queued'])


@login_required
def detail(request, pk, download=False, source=False):
    items = Contribution.objects.all()
    if not request.user.has_perm('fgtida.view_contribution'):
        items = items.filter(submitter=request.user)
    item = get_object_or_404(items, pk=pk)
    if download or source:
        from django.utils.http import content_disposition_header
        if source and not item.source_archive:
            raise Http404
        response = HttpResponse(bytes(item.source_archive if source else item.content), content_type='application/octet-stream')
        response['Content-Disposition'] = content_disposition_header(True, 'source.zip' if source else item.filename)
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-store'
        return response
    chapters = []
    assets = []
    if item.source_archive:
        from .source_packages import unpack
        for name, data in unpack(bytes(item.source_archive)).items():
            if name.endswith('.rst'):
                chapters.append((name, data.decode('utf-8-sig')))
            else:
                assets.append(name)
    return render(request, 'fgtida/contribution.html', {'submission': item, 'chapters': chapters, 'assets': assets,
        'can_verify': item.submission_method == 'word' and item.submitter_id == request.user.pk and not item.conversion_verified_at})


@login_required
@require_POST
@transaction.atomic
def verify_conversion(request, pk):
    item = get_object_or_404(Contribution.objects.select_for_update(), pk=pk, submitter=request.user, submission_method='word', status='pending')
    if request.POST.get('verified') != 'yes':
        return HttpResponse('Confirm that you checked the converted content and figures.', status=400)
    if not item.conversion_verified_at:
        item.conversion_verified_at = timezone.now()
        item.save(update_fields=['conversion_verified_at'])
        core.save_document(item.document, core.actor_for(request.user), 'Contributor verified the automatic Word-to-RST conversion.')
    return redirect('fgtida:contribution', pk=pk)


@login_required
def asset(request, pk, path):
    items = Contribution.objects.all()
    if not request.user.has_perm('fgtida.view_contribution'):
        items = items.filter(submitter=request.user)
    item = get_object_or_404(items, pk=pk)
    if not item.source_archive:
        raise Http404
    from .source_packages import unpack
    data = unpack(bytes(item.source_archive)).get(path)
    if data is None:
        raise Http404
    from django.utils.http import content_disposition_header
    mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif'}.get(Path(path).suffix.lower())
    response = HttpResponse(data, content_type=mime or 'application/octet-stream')
    response['Content-Disposition'] = content_disposition_header(not bool(mime), Path(path).name)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response['Cache-Control'] = 'private, no-store'
    return response


@transaction.atomic
def accept(pk, user, target_group=None):
    item = Contribution.objects.select_for_update().get(pk=pk)
    if item.status != 'pending':
        return
    from django.core.exceptions import ValidationError
    if item.submission_method == 'word' and not item.conversion_verified_at:
        raise ValidationError('The contributor must verify the Word-to-RST conversion before adoption.')
    from django.utils.text import slugify
    incubation=item.group.acronym=='incubation'
    group=target_group or item.group
    if group.type_id!='wg' or not core.groups().filter(pk=group.pk).exclude(state_id='conclude').exists():
        raise ValidationError('Select the working group adopting this item. Incubation must form or select a WG before adoption.')
    doc=item.document
    if not doc:
        doc=Document(name=f'fg-tida-contribution-{item.pk}',title=item.title,abstract=item.abstract,type_id='tida',group=group)
        core.save_document(doc,core.actor_for(user),'Original submitted file archived.',data=bytes(item.content),filename=item.filename,
            state=State.objects.get(type_id='tida',slug='submitted'))
        DocumentAuthor.objects.create(document=doc,person=core.actor_for(item.submitter),order=1)
    if doc.group_id!=group.pk:
        doc.group=group
        core.save_document(doc,core.actor_for(user),f'Assigned to adopting group: {group.name}.')
    from .work_items import transition
    transition(doc,'draft',core.actor_for(user),f'Contribution {item.pk} adopted by {group.name}.')
    work,_=WorkItem.objects.get_or_create(document=doc,defaults={'short_name':item.work_item_acronym or slugify(item.title)[:48] or f'item-{item.pk}'})
    # An adopted item is now edited in its official repository. The original
    # issue remains provenance and cannot overwrite the adopted document.
    from .models import GitHubSource
    GitHubSource.objects.filter(document=doc).update(sync_mode='manual')
    GitHubJob.objects.get_or_create(kind='provision',work_item=work,key=f'provision:{work.pk}')
    if incubation and not group.charter_id:
        charter=Document(name=f'fg-tida-{group.acronym}-charter',title=f'{group.name} — charter',type_id='charter',group=group)
        core.save_document(charter,core.actor_for(user),'Draft charter required after Incubation adoption.',
            state=State.objects.get(type_id='charter',slug='draft'))
        charter_work=WorkItem.objects.create(document=charter,short_name='charter')
        GitHubJob.objects.create(kind='provision',work_item=charter_work,key=f'provision:{charter_work.pk}')
        group.charter=charter
        group.save(update_fields=['charter'])
    # Keep the original submission target for notification routing and review
    # history. The public document now belongs to the adopting working group.
    item.document = doc
    item.status = 'accepted'
    item.reviewed_by = user
    item.reviewed_at = timezone.now()
    item.save()


@transaction.atomic
def reject(pk,user):
    item=Contribution.objects.select_for_update().get(pk=pk)
    if item.status!='pending':
        return
    if item.document_id:
        from .work_items import transition
        transition(item.document,'rejected',core.actor_for(user),'Proposal rejected after group review.')
    item.status='rejected'
    item.reviewed_by=user
    item.reviewed_at=timezone.now()
    item.save(update_fields=['status','reviewed_by','reviewed_at'])
