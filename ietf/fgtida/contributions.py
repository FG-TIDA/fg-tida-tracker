from pathlib import Path
from datetime import timezone as utc_timezone
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
from .models import Contribution, DocumentVersion, WorkItem, GitHubJob
from .credit_lists import parse_credit_list, format_credit_list


class CreditListField(forms.CharField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('required', False)
        kwargs.setdefault('max_length', 40000)
        kwargs.setdefault('widget', forms.Textarea(attrs={'rows': 2}))
        kwargs.setdefault('help_text', 'Separate entries with commas or new lines. Put an entry containing a comma in double quotes. Accounts are not required for co-authors.')
        super().__init__(*args, **kwargs)

    def clean(self, value):
        value = super().clean(value)
        try:
            return '\n'.join(parse_credit_list(value))
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error


class ContributionForm(forms.ModelForm):
    authors = CreditListField(label='Author(s)', help_text='Enter all authors, separated by commas or new lines. Co-authors need no tracker account. Leave blank to use the document source. Your submitting account is recorded separately.')
    affiliations = CreditListField(label='Affiliation(s)', help_text='Separate organizations with commas or new lines. Put an organization containing a comma in double quotes. Leave blank to use the source.')
    meeting = forms.ModelChoiceField(queryset=core.meetings().none(), required=True,
        empty_label='Choose an upcoming meeting', help_text='Select the meeting you are contributing to. Closed submission windows are unavailable.',
        error_messages={'invalid_choice': 'This meeting is unavailable or its submission deadline has passed. Choose an open meeting.'})
    submission_method = forms.ChoiceField(choices=[('github', 'GitHub repository'), ('rst', 'RST source ZIP'),
        ('word', 'Word (.docx) — exceptional fallback')], label='How will you submit?')
    upload = forms.FileField(required=False, help_text='RST: upload your completed ZIP. Word fallback: upload .docx. Maximum 10 MB. Leave empty for GitHub.')
    publication_consent = forms.BooleanField(label='I understand that the confirmed contribution and its source files will be public.')

    class Meta:
        model = Contribution
        fields = ['title', 'group', 'meeting', 'authors', 'affiliations', 'work_item_acronym', 'submission_method',
                  'draft_repository_url', 'source_commit', 'source_folder', 'entry_document']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .meeting_submissions import open_meetings, submission_deadline
        self.fields['meeting'].queryset = open_meetings()
        self.fields['meeting'].label_from_instance = lambda meeting: f'{meeting.display_title} — {meeting.date:%d %b %Y} (deadline {submission_deadline(meeting).astimezone(utc_timezone.utc):%d %b %Y %H:%M UTC})'
        from django.db.models import Q
        self.fields['group'].queryset = core.groups().filter(Q(type_id='wg') | Q(acronym='incubation')).order_by('tida_configuration__number','name')
        from .templatetags.fgtida import group_label
        self.fields['group'].label_from_instance = group_label
        self.fields['group'].queryset = self.fields['group'].queryset.exclude(state_id='conclude')
        self.fields['group'].label = 'Working group or Incubation'
        self.fields['group'].help_text = 'Choose an existing working group or the Incubation submission route. Incubation is not a working group.'
        self.fields['draft_repository_url'].help_text = 'GitHub only: your public repository in your own GitHub account, not the template URL.'
        self.fields['source_commit'].help_text = 'GitHub only: copy the full 40-character commit SHA of your completed contribution.'
        self.fields['source_folder'].help_text = 'GitHub only: documentation folder relative to the repository root, normally src/doc. All referenced assets must be inside it.'
        self.fields['entry_document'].help_text = 'GitHub: path from repository root, e.g. src/doc/index.rst. ZIP: full path inside the ZIP, e.g. my-contribution/src/doc/index.rst. Leave empty for Word.'
        self.fields['work_item_acronym'].help_text = 'Optional short identifier for the work item repository.'
        self.fields['upload'].help_text += ' Submitting creates a public work-item record and archives this file as version 0.0.'

    def clean_meeting(self):
        from .meeting_submissions import submissions_open
        meeting = self.cleaned_data['meeting']
        if not submissions_open(meeting):
            raise forms.ValidationError('Submissions for this meeting are closed.')
        return meeting

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
                    self.source_archive, self.manifest = prepare_zip(self.content, entry, '' if folder == '.' else folder, complete=True)
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
    if request.method == 'POST' and any(request.POST.get(field) for field in
            ('submitter', 'contact_name', 'contact_email', 'contact_affiliation')):
        return HttpResponse('Submitting on behalf of another person is available only in the administrator backend.', status=403)
    from .mfa import allow_attempt
    if request.method == 'POST' and not allow_attempt(f'file-submission:{request.user.pk}', limit=20):
        return HttpResponse('Too many file submissions. Please wait 15 minutes before trying again.', status=429,
                            headers={'Retry-After': '900'})
    selected = core.groups().filter(acronym=request.GET.get('group', ''), acronym__in=['incubation']).first()
    form = ContributionForm(request.POST or None, request.FILES or None, initial={'group': selected} if selected else None)
    if request.method == 'POST' and form.is_valid():
        # Serialize a user's submissions to enforce a bounded pending queue.
        with transaction.atomic():
            from .meeting_submissions import lock_open_meeting
            try:
                meeting = lock_open_meeting(form.cleaned_data['meeting'])
                form.cleaned_data['meeting'] = form.instance.meeting = meeting
            except forms.ValidationError as error:
                form.add_error('meeting', error)
                return render(request, 'fgtida/submit.html', {'form': form, 'submissions': [], 'page': None, 'queryargs': ''})
            from django.contrib.auth import get_user_model
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if Contribution.objects.filter(submitter=request.user, status='pending').count() >= 20:
                form.add_error(None, 'You already have 20 contributions awaiting review. Contact the secretariat.')
            else:
                item = form.save(commit=False)
                item.submitter = request.user
                record_submission(item, form, request.user)
                return redirect('fgtida:contribution', pk=item.pk)
    page = Paginator(Contribution.objects.filter(submitter=request.user).defer('content', 'source_archive'), 25).get_page(request.GET.get('page'))
    return render(request, 'fgtida/submit.html', {'form': form, 'submissions': page, 'page': page, 'queryargs': ''})


@transaction.atomic
def record_submission(item, form, user):
    from django.utils.text import slugify
    from .email_templates import contribution
    from .github_attribution import source_attribution
    from .mail import enqueue
    from .source_packages import package_text

    item.content = form.content
    item.notifications_queued = False
    item.filename = form.filename
    item.source_archive = form.source_archive
    item.source_manifest = form.manifest
    item.save()
    if item.submitter_id:
        core.actor_for(item.submitter)
    credits = source_attribution(package_text(form.source_archive))
    if item.contact_name:
        credits.update(authors=list(dict.fromkeys([*credits.get('authors', []), item.contact_name])), contacts=[item.contact_name])
    if item.contact_affiliation:
        credits['affiliations'] = list(dict.fromkeys([*credits.get('affiliations', []), item.contact_affiliation]))
    if item.contact_email:
        credits['contact_emails'] = [item.contact_email]
    entered = {field: form.cleaned_data.get(field, '') for field in ('authors', 'affiliations')}
    for field, value in entered.items():
        if value:
            credits[field] = value.splitlines()
    doc = Document(name=f'fg-tida-contribution-{item.pk}', title=item.title,
        abstract=item.abstract, type_id='tida', group=item.group)
    core.save_document(doc, core.actor_for(user), 'Work item submitted for a group decision.',
        data=bytes(item.source_archive), filename='source.zip', state=State.objects.get(type_id='tida', slug='submitted'),
        source_metadata=credits)
    if item.submitter and not credits.get('authors') and not credits.get('contacts'):
        DocumentAuthor.objects.create(document=doc, person=core.actor_for(item.submitter), order=1)
    item.document = doc
    item.save(update_fields=['document'])
    from .file_safety import retire_private_after_archive
    retire_private_after_archive(bytes(item.content), item.filename, contribution_id=item.pk)
    sync_contact_metadata(item)
    if any(entered.values()):
        from .models import DocumentAttribution
        from .credits import record_editor_changes
        metadata, _ = DocumentAttribution.objects.get_or_create(document=doc)
        fields = [field for field, value in entered.items() if value]
        for field in fields:
            setattr(metadata, field, entered[field])
        record_editor_changes(metadata, fields)
        metadata.save()
    WorkItem.objects.create(document=doc, short_name=item.work_item_acronym or slugify(item.title)[:48] or f'item-{item.pk}')
    queue_notifications(item.pk)
    receipt_address = item.contact_email or (item.submitter.email if item.submitter else '')
    if receipt_address:
        subject, body = contribution(item, acknowledgement=True)
        enqueue(receipt_address, subject, body, contribution=item, purpose='submitter')


def sync_contact_metadata(item, clear=False, previous=None):
    if not item.document_id:
        return
    from .models import DocumentAttribution
    values = {'authors': item.contact_name, 'contacts': item.contact_name,
              'affiliations': item.contact_affiliation, 'contact_emails': item.contact_email}
    if any(values.values()) or clear:
        attribution, created = DocumentAttribution.objects.get_or_create(document_id=item.document_id)
        version = item.document.tida_versions.filter(revision=item.document.rev).first()
        source = version.source_metadata if version else {}
        for key, contact_field in [('authors', 'contact_name'), ('affiliations', 'contact_affiliation')]:
            lines = list(source.get(key, [])) if created else getattr(attribution, key).splitlines()
            old = (previous or {}).get(contact_field, '')
            if old and old != getattr(item, contact_field):
                lines = [getattr(item, contact_field) if line == old else line for line in lines]
                lines = [line for line in lines if line]
            if getattr(item, contact_field):
                lines.append(getattr(item, contact_field))
            values[key] = '\n'.join(dict.fromkeys(lines))
        provenance = dict(attribution.credit_provenance or {})
        updates = []
        contact_fields = {'authors': 'contact_name', 'contacts': 'contact_name',
                          'affiliations': 'contact_affiliation', 'contact_emails': 'contact_email'}
        for key, value in values.items():
            if clear and previous is not None and previous.get(contact_fields[key], '') == getattr(item, contact_fields[key]):
                continue
            mode = provenance.get(key, {}).get('mode')
            if not clear and (mode in ('manual', 'profiles') or (not created and mode is None and getattr(attribution, key))):
                continue
            setattr(attribution, key, value)
            provenance[key] = {'mode': 'manual'} if clear else {'mode': 'source', 'value': value}
            updates.append(key)
        attribution.credit_provenance = provenance
        attribution.save(update_fields=[*updates, 'credit_provenance'])


@transaction.atomic
def convert_existing_word(pk, user, *, rebuild=False, reason=''):
    """Archive a maintained source package without duplicating the work item."""
    item = Contribution.objects.select_for_update().get(pk=pk)
    previous_package = bytes(item.source_archive)
    if previous_package and not rebuild:
        return False
    from .word_scaffold import PROFILE
    if previous_package and item.source_manifest.get('package_profile') == PROFILE:
        from .word_scaffold import package_gaps
        from .source_packages import unpack
        if not any(package_gaps(unpack(previous_package), bytes(item.content)).values()):
            return False
    if previous_package and (item.submission_method not in {'word', 'legacy'} or len(reason.strip()) < 10):
        raise forms.ValidationError('Rebuilding an existing conversion requires an explicit administrator review reason.')
    if Path(item.filename).suffix.lower() != '.docx':
        raise forms.ValidationError('Only DOCX submissions can be converted automatically. Save older DOC files as DOCX first.')
    from .source_packages import convert_word
    from .models import DocumentFormat
    import hashlib
    if previous_package:
        from .file_safety import preview_source
        if not item.document_id or preview_source(item.document)[0] != previous_package:
            raise forms.ValidationError('The current document differs from this conversion. Review later revisions instead of replacing edited content.')
        if item.source_manifest.get('sha256') != hashlib.sha256(previous_package).hexdigest():
            raise forms.ValidationError('The previous source package was modified. Review it before rebuilding from Word.')
    archive, manifest = convert_word(bytes(item.content))
    item.source_archive = archive
    item.source_manifest = {**manifest, 'original_sha256': hashlib.sha256(bytes(item.content)).hexdigest(), 'method': 'word'}
    item.submission_method = 'word'
    item.entry_document = 'index.rst'
    item.conversion_verified_at = None
    item.conversion_verified_by = None
    doc = item.document or Document(name=f'fg-tida-contribution-{item.pk}', title=item.title,
        abstract=item.abstract, type_id='tida', group=item.group)
    if previous_package:
        from .versioning import next_version
        core.save_document(doc, core.actor_for(user),
            'Word source package rebuilt with specification scaffold; verification reset. ' + reason.strip(),
            data=archive, filename='source.zip', version=next_version(doc))
        DocumentFormat.objects.get_or_create(document=doc, revision=doc.rev, kind='docx', defaults={
            'filename': item.filename, 'content': bytes(item.content),
            'sha256': hashlib.sha256(bytes(item.content)).hexdigest()})
    elif doc.pk and doc.uploaded_filename:
        current = Path(doc.get_file_name())
        if not current.is_file() or current.read_bytes() != bytes(item.content):
            raise forms.ValidationError('The current document differs from the original Word submission. Review its revisions before converting an older original.')
        core.save_document(doc, core.actor_for(user),
            'Maintained RST ZIP added for the existing Word version. Conversion requires verification; original and version preserved.')
        package, created = DocumentFormat.objects.get_or_create(document=doc, revision=doc.rev, kind='zip',
            defaults={'filename': 'source.zip', 'content': archive, 'sha256': hashlib.sha256(archive).hexdigest()})
        if not created and bytes(package.content) != archive:
            raise forms.ValidationError('This version already has a different RST ZIP package. Review that package before replacing it.')
    else:
        core.save_document(doc, core.actor_for(user), 'Word converted to a maintained RST package; verification required.',
            data=archive, filename='source.zip', state=State.objects.get(type_id='tida', slug='submitted'))
        DocumentFormat.objects.get_or_create(document=doc, revision=doc.rev, kind='docx', defaults={
            'filename': item.filename, 'content': bytes(item.content),
            'sha256': hashlib.sha256(bytes(item.content)).hexdigest()})
    item.document = doc
    item.save(update_fields=['source_archive', 'source_manifest', 'submission_method', 'entry_document',
        'conversion_verified_at', 'conversion_verified_by', 'document'])
    sync_contact_metadata(item)
    from django.utils.text import slugify
    WorkItem.objects.get_or_create(document=doc, defaults={'short_name': item.work_item_acronym or slugify(item.title)[:48] or f'item-{item.pk}'})
    return True


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
    from .file_safety import allowed, require_clean
    if download or source:
        from django.utils.http import content_disposition_header
        if source and not item.source_archive:
            raise Http404
        require_clean(bytes(item.source_archive if source else item.content), 'source.zip' if source else item.filename)
        response = HttpResponse(bytes(item.source_archive if source else item.content), content_type='application/octet-stream')
        response['Content-Disposition'] = content_disposition_header(True, 'source.zip' if source else item.filename)
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-store'
        return response
    chapters = []
    assets = []
    security_pending = bool(item.source_archive) and not allowed(bytes(item.source_archive), 'source.zip')
    if item.source_archive and not security_pending:
        from .source_packages import unpack
        from .package_previews import chapters as package_chapters
        from django.urls import reverse
        chapters = package_chapters(bytes(item.source_archive),
            lambda path: reverse('fgtida:contribution-asset', args=[item.pk, path]))
        for name, data in unpack(bytes(item.source_archive)).items():
            if not name.endswith('.rst'):
                assets.append(name)
    legacy_text = ''
    if not chapters and item.content and allowed(bytes(item.content), item.filename):
        from .files import extract_text
        try:
            legacy_text = extract_text(bytes(item.content), item.filename)
        except forms.ValidationError:
            pass  # Keep the original download available when extraction is unsupported.
    from .rendering import preview_abstract
    display_abstract = preview_abstract(item.abstract, [text for _, text, _ in chapters] + [legacy_text])
    return render(request, 'fgtida/contribution.html', {'submission': item, 'chapters': chapters, 'assets': assets,
        'display_abstract': display_abstract,
        'security_pending': security_pending, 'original_available': allowed(bytes(item.content), item.filename),
        'legacy_text': legacy_text, 'legacy_extension': Path(item.filename).suffix.lower(),
        'can_revise': can_revise(item, request.user),
        'can_verify': item.submission_method == 'word' and item.submitter_id == request.user.pk and not item.conversion_verified_at
                      and not security_pending and allowed(bytes(item.content), item.filename)})


def can_revise(item, user):
    return item.status == 'pending' and bool(item.document_id) and (user is None or item.submitter_id == user.pk or (
        user.has_perm('fgtida.change_contribution') and user.has_perm('doc.change_document')))


class ContributionCreditsForm(forms.Form):
    authors = CreditListField(label='Author(s)')
    affiliations = CreditListField(label='Affiliation(s)')


@login_required
def edit_credits(request, pk):
    items = Contribution.objects.select_related('document')
    if not (request.user.has_perm('fgtida.change_contribution') and request.user.has_perm('doc.change_document')):
        items = items.filter(submitter=request.user)
    item = get_object_or_404(items, pk=pk)
    if not can_revise(item, request.user):
        return HttpResponse('Author details for an adopted or closed work item must be changed by an administrator in the backend.', status=403)
    if request.method == 'POST' and any(request.POST.get(field) for field in ('submitter', 'contact_name', 'contact_email', 'contact_affiliation')):
        return HttpResponse('The submitting account cannot be changed through this form.', status=403)
    from .models import DocumentAttribution
    from .github_attribution import source_attribution
    from .file_safety import safe_document_text
    from .credits import clean_values, record_editor_changes
    metadata = getattr(item.document, 'tida_attribution', None)
    declared = source_attribution(safe_document_text(item.document) or '')
    assigned = list(item.document.documentauthor_set.select_related('person').order_by('order'))
    native = {'authors': [author.person.name for author in assigned],
              'affiliations': list(dict.fromkeys(author.affiliation for author in assigned if author.affiliation))}
    initial = {}
    for field in ('authors', 'affiliations'):
        mode = (getattr(metadata, 'credit_provenance', {}) or {}).get(field, {}).get('mode')
        value = getattr(metadata, field, '')
        if mode == 'profiles':
            value = native[field]
        elif mode != 'manual' and not value:
            value = declared.get(field, []) or native[field]
        initial[field] = format_credit_list(clean_values(value, field))
    form = ContributionCreditsForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            item = Contribution.objects.select_for_update().get(pk=item.pk)
            if not can_revise(item, request.user):
                return HttpResponse('This work item is no longer awaiting review.', status=403)
            item.document = Document.objects.select_for_update().get(pk=item.document_id)
            metadata, _ = DocumentAttribution.objects.select_for_update().get_or_create(document_id=item.document_id)
            for field in ('authors', 'affiliations'):
                setattr(metadata, field, form.cleaned_data[field])
            record_editor_changes(metadata, ('authors', 'affiliations'))
            metadata.save()
            core.save_document(item.document, core.actor_for(request.user), 'Author(s) and affiliations updated by the recorded submitter/editor. Submitting account and archived sources unchanged.')
        return redirect('fgtida:document', name=item.document.name)
    response = render(request, 'fgtida/edit_credits.html', {'submission': item, 'form': form})
    response['Cache-Control'] = 'private, no-store'
    return response


class ContributionRevisionForm(forms.Form):
    repository = forms.URLField(max_length=300, label='Public GitHub repository')
    commit = forms.RegexField(r'\A[0-9a-fA-F]{40}\Z', min_length=40, max_length=40, label='Full commit SHA')
    source_folder = forms.CharField(max_length=255, initial='src/doc')
    entry_document = forms.CharField(max_length=255, initial='src/doc/index.rst')
    version = forms.CharField(max_length=32, help_text='Use X.Y notation. The version must be higher than the latest archived version.')
    publication_consent = forms.BooleanField(label='I confirm these source files and this revision will be public.')

    def clean_version(self):
        from .versioning import parts
        version = self.cleaned_data['version']
        parts(version)
        return version


def archive_github_revision(pk, actor, repository, commit, source_folder, entry_document, version):
    from .github_client import repository_name
    from .github_attribution import source_attribution
    from .source_packages import github_snapshot, unpack
    from .versioning import next_version

    repository = 'https://github.com/' + repository_name(repository)
    commit = commit.lower()
    item = Contribution.objects.select_related('document').get(pk=pk)
    if not can_revise(item, actor):
        raise forms.ValidationError('Only the submitter or an authorized editor can revise a work item awaiting review.')
    next_version(item.document, version)
    archive, manifest = github_snapshot(repository, commit, source_folder, entry_document)
    credits = source_attribution(unpack(archive)[manifest['entry_document']].decode('utf-8-sig'))
    with transaction.atomic():
        item = Contribution.objects.select_for_update().get(pk=pk)
        if not can_revise(item, actor):
            raise forms.ValidationError('This work item is no longer awaiting review.')
        document = Document.objects.select_for_update().get(pk=item.document_id)
        from .work_items import state_of
        if state_of(document) != 'submitted':
            raise forms.ValidationError('Only a work item in Submitted state can receive a revision through this form.')
        if DocumentVersion.objects.filter(document=document, source_metadata__snapshot__commit=commit,
                source_metadata__snapshot__sha256=manifest['sha256']).exists():
            raise forms.ValidationError('This GitHub commit and source package are already archived for this work item.')
        if item.source_manifest.get('commit') == commit and item.source_manifest.get('sha256') == manifest['sha256']:
            raise forms.ValidationError('This is the original submission commit and source package.')
        core.save_document(document, core.actor_for(actor), f'Archived work item revision {version} from {repository}/commit/{commit}.',
            data=archive, filename='source.zip', version=version, source_metadata={**credits, 'snapshot': manifest})
        return document


@login_required
def revise(request, pk):
    item = get_object_or_404(Contribution.objects.defer('content', 'source_archive'), pk=pk)
    if not can_revise(item, request.user):
        raise Http404
    from .mfa import allow_attempt
    if request.method == 'POST' and not allow_attempt(f'file-submission:{request.user.pk}', limit=20):
        return HttpResponse('Too many file submissions. Please wait 15 minutes before trying again.', status=429,
                            headers={'Retry-After': '900'})
    form = ContributionRevisionForm(request.POST if request.method == 'POST' else None, initial={
        'repository': item.draft_repository_url,
        'source_folder': item.source_folder or 'src/doc',
        'entry_document': item.entry_document if item.submission_method == 'github' else 'src/doc/index.rst',
    })
    if request.method == 'POST' and form.is_valid():
        values = {key: value for key, value in form.cleaned_data.items() if key != 'publication_consent'}
        try:
            document = archive_github_revision(item.pk, request.user, **values)
        except forms.ValidationError as error:
            form.add_error(None, error)
        else:
            return redirect('fgtida:revision', name=document.name, rev=values['version'])
    return render(request, 'fgtida/contribution_revision.html', {'submission': item, 'form': form})


@login_required
@require_POST
@transaction.atomic
def verify_conversion(request, pk):
    item = get_object_or_404(Contribution.objects.select_for_update(), pk=pk, submitter=request.user, submission_method='word', status='pending')
    from .file_safety import require_clean
    require_clean(bytes(item.content), item.filename)
    require_clean(bytes(item.source_archive), 'source.zip')
    if request.POST.get('verified') != 'yes':
        return HttpResponse('Confirm that you checked the converted content and figures.', status=400)
    if not item.conversion_verified_at:
        item.conversion_verified_at = timezone.now()
        item.conversion_verified_by = request.user
        item.save(update_fields=['conversion_verified_at', 'conversion_verified_by'])
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
    from .file_safety import require_clean
    require_clean(bytes(item.source_archive), 'source.zip')
    from .package_previews import IMAGE_TYPES, image_response
    if Path(path).suffix.lower() in IMAGE_TYPES:
        return image_response(bytes(item.source_archive), path)
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
    from .file_safety import validate_file
    if item.content:
        validate_file(bytes(item.content), item.filename)
    if item.source_archive:
        validate_file(bytes(item.source_archive), 'source.zip')
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
        if item.submitter:
            DocumentAuthor.objects.create(document=doc,person=core.actor_for(item.submitter),order=1)
        sync_contact_metadata(item)
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
    item.save(update_fields=['document', 'status', 'reviewed_by', 'reviewed_at'])


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
