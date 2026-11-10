"""Personalize upstream admins without replacing their database models."""
from types import MethodType
from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from urllib.parse import urlsplit

from ietf.doc.admin import DocumentAdmin, DocAuthorInline, RelatedDocumentInline, AdditionalUrlInLine
from ietf.doc.models import Document, State
from ietf.doc.utils import save_document_in_history
from ietf.group.admin import GroupAdmin, GroupForm, RoleInline, GroupURLInline
from ietf.group.models import Group, Role, GroupEvent
from ietf.group.utils import save_group_in_history
from ietf.meeting.admin import MeetingAdmin, SessionAdmin
from ietf.meeting.models import Meeting, MeetingCategory, Session, Schedule, SessionPresentation, SchedTimeSessAssignment, TimeSlot, SchedulingEvent, Room
from ietf.name.models import DocTypeName, DocTagName, ImportantDateName
from ietf.name.admin import ImportantDateNameAdmin
from . import core
from . import workflow
from .files import extract_text, MAX_BYTES
from .models import Contribution, ContributionRecipient, SessionResource, OutgoingMail, AdministratorApproval, SecurityQuestion
from .models import (EmailVerification, GitHubAccount, WorkingGroupConfiguration, GitHubRepository,
    GitHubSource, WorkItem, WorkItemRelease, GitHubJob, DocumentListing, DocumentFormat)
from .resource_files import SessionResourceForm
from django.contrib.auth.models import User
from ietf.ietfauth.admin import CustomUserAdmin
from django.utils.html import format_html
from django.utils import timezone
from django.urls import reverse, NoReverseMatch
from ietf.person.models import Person
from ietf.person.admin import PersonAdmin, EmailInline, AliasInline, PersonUUIDInline


class FgEmailInline(EmailInline):
    # Email's string primary key defaults to ''. Django treats that as a set
    # key on an empty inline and cannot reverse its change link.
    show_change_link = False
    extra = 0


class SecurityQuestionAdmin(admin.ModelAdmin):
    list_display = ['prompt', 'active', 'order']
    search_fields = ['prompt', 'slug']
    list_filter = ['active']
    fields = ['slug', 'prompt', 'active', 'order']

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission

    def has_delete_permission(self, request, obj=None):
        return False  # Retire questions without breaking enrolled accounts.

    def get_readonly_fields(self, request, obj=None):
        return ['slug'] if obj else []

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)
        class QuestionForm(base):
            def clean(self):
                values = super().clean()
                if not values.get('active') and SecurityQuestion.objects.filter(active=True).exclude(pk=self.instance.pk).count() < 3:
                    self.add_error('active', 'Keep at least three active security questions.')
                return values
        return QuestionForm


class FgPersonAdmin(PersonAdmin):
    inlines = [FgEmailInline, AliasInline, PersonUUIDInline]


def can_delete_account_credential(request, obj=None):
    return (obj is not None and request.user.is_superuser
            and obj.user_id in getattr(request, '_fgtida_deleting_user_ids', ()))


class FgUserAdmin(CustomUserAdmin):
    actions = None
    add_fieldsets = CustomUserAdmin.add_fieldsets + (
        ('Contact and administrator access', {'fields': ('email', 'is_staff', 'is_superuser')}),
    )

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and (obj is None or obj.pk != request.user.pk)

    def get_deleted_objects(self, objs, request):
        # Django checks each registered child admin during cascade previews.
        # Allow account-owned credentials only within this user deletion.
        objs = list(objs)
        request._fgtida_deleting_user_ids = {obj.pk for obj in objs if self.has_delete_permission(request, obj)}
        try:
            return super().get_deleted_objects(objs, request)
        finally:
            del request._fgtida_deleting_user_ids

    @transaction.atomic
    def delete_model(self, request, obj):
        # Serialize administrator deletions so two admins cannot delete each
        # other's accounts concurrently and leave the site without an admin.
        admins = set(User.objects.select_for_update().filter(is_active=True, is_superuser=True)
                     .order_by('pk').values_list('pk', flat=True))
        if request.user.pk not in admins or not self.has_delete_permission(request, obj):
            raise PermissionDenied
        from ietf.person.models import Email
        from django.db.models import Q
        addresses = {address.lower() for address in Email.objects.filter(person__user=obj).values_list('address', flat=True)}
        if obj.email:
            addresses.add(obj.email.lower())
        OutgoingMail.objects.filter(sent__isnull=True).filter(
            Q(recipient__in=addresses, purpose__in=['verification', 'reset']) |
            Q(contribution__submitter=obj, purpose='submitter')
        ).update(expires=timezone.now(), last_error='Account deleted; delivery cancelled.')
        super().delete_model(request, obj)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        person = core.actor_for(obj)
        if obj.email:
            from ietf.person.models import Email
            Email.objects.get_or_create(address=obj.email.lower(), defaults={'person': person, 'origin': 'FG-TIDA administration'})
        if obj.is_staff or obj.is_superuser:
            AdministratorApproval.objects.update_or_create(user=obj, defaults={'approved_by': request.user})
        else:
            AdministratorApproval.objects.filter(user=obj).delete()


class OutgoingMailAdmin(admin.ModelAdmin):
    list_display = ['subject', 'recipient', 'created', 'sent', 'expires', 'attempts', 'last_error']
    readonly_fields = ['recipient', 'subject', 'body', 'contribution', 'created', 'sent', 'expires', 'attempts', 'last_error']
    actions = ['retry_delivery']

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.action(description='Retry delivery of selected messages', permissions=['change'])
    def retry_delivery(self, request, queryset):
        from .mail import deliver
        count = sum(deliver(pk) for pk in queryset.values_list('pk', flat=True))
        self.message_user(request, f'{count} messages accepted by the configured mail backend. SMTP must be enabled for delivery.')


class SessionResourceInline(admin.TabularInline):
    model = SessionResource
    form = SessionResourceForm
    fields = ['kind', 'title', 'upload', 'attachment', 'url']
    readonly_fields = ['attachment']
    extra = 0

    def attachment(self, obj):
        if obj and obj.file_size:
            return format_html('<a href="{}">{}</a> ({} bytes)', obj.resource_url, obj.filename, obj.file_size)
        return 'No file attached'


class ContributionReviewForm(forms.ModelForm):
    decision = forms.ChoiceField(required=False, choices=[('','Save review note'),('adopt','Adopt into a working group'),('reject','Reject proposal')])
    adopt_into = forms.ModelChoiceField(queryset=Group.objects.filter(parent__acronym='tida',type_id='wg').exclude(state_id='conclude'),required=False,
        help_text='For Incubation, create the new WG and its numbered GitHub configuration first, then select it here.')

    class Meta:
        model=Contribution
        fields=['review_note']

    def clean(self):
        values=super().clean()
        if values.get('decision')=='adopt' and self.instance.group.acronym=='incubation' and not values.get('adopt_into'):
            self.add_error('adopt_into','Select the working group formed to adopt this proposal.')
        if values.get('decision') and self.instance.status!='pending':
            self.add_error('decision','This proposal already has a review decision. Use its document lifecycle for further changes.')
        return values


class ContributionAdmin(admin.ModelAdmin):
    form=ContributionReviewForm
    list_display = ['title', 'submitter', 'group', 'created', 'status', 'notifications_queued']
    list_filter = ['status', 'group']
    search_fields = ['title', 'submitter__username']
    fields = ['title', 'submitter', 'group', 'abstract', 'submitted_file', 'created',
              'status', 'review_note', 'decision', 'adopt_into', 'draft_repository_url', 'reviewed_by', 'reviewed_at', 'document', 'notifications_queued']
    readonly_fields = [field for field in fields if field not in {'review_note','decision','adopt_into'}]
    actions = ['accept_contributions', 'reject_contributions']

    def save_model(self,request,obj,form,change):
        super().save_model(request,obj,form,change)
        from .contributions import accept,reject
        if form.cleaned_data.get('decision')=='adopt':
            if not request.user.has_perm('doc.add_document'):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied
            accept(obj.pk,request.user,form.cleaned_data.get('adopt_into'))
        elif form.cleaned_data.get('decision')=='reject':
            reject(obj.pk,request.user)

    def get_queryset(self, request):
        return super().get_queryset(request).defer('content').select_related('submitter', 'group')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def submitted_file(self, obj):
        return format_html('<a href="{}">{}</a>', reverse('fgtida:contribution-download', args=[obj.pk]), obj.filename)

    @admin.action(description='Accept and publish selected pending contributions', permissions=['change'])
    def accept_contributions(self, request, queryset):
        from .contributions import accept
        if not request.user.has_perm('doc.add_document'):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        for item in queryset.filter(status='pending'):
            try:
                accept(item.pk, request.user)
            except ValidationError as error:
                self.message_user(request,'; '.join(error.messages),level='error')
        self.message_user(request, 'Eligible proposals adopted as Draft. Repository creation remains queued until GitHub is configured.')

    @admin.action(description='Reject selected pending contributions', permissions=['change'])
    def reject_contributions(self, request, queryset):
        from .contributions import reject
        for item in queryset.filter(status='pending'):
            reject(item.pk,request.user)


class SessionResourceAdmin(admin.ModelAdmin):
    form = SessionResourceForm
    fields = ['session', 'kind', 'title', 'upload', 'attachment', 'url']
    readonly_fields = ['attachment']
    attachment = SessionResourceInline.attachment
    list_display = ['title', 'kind', 'session']
    search_fields = ['title', 'session__name', 'session__meeting__number']
    list_filter = ['kind']
    list_select_related = ['session', 'session__meeting']
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'session':
            kwargs['queryset'] = Session.objects.filter(meeting__type_id='tida', group__in=core.groups())
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(session__meeting__type_id='tida', session__group__in=core.groups())


class MemberRoleForm(forms.ModelForm):
    class Meta:
        model = Role
        fields = '__all__'

    def clean(self):
        values = super().clean()
        person, email = values.get('person'), values.get('email')
        if person and email and email.person_id != person.pk:
            self.add_error('email', 'Choose an email belonging to this person.')
        return values


class MemberRoleInline(RoleInline):
    form = MemberRoleForm
    extra = 0

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'name':
            kwargs['queryset'] = db_field.remote_field.model.objects.filter(slug__in=['chair', 'secr', 'member'])
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class FgRoleAdmin(admin.ModelAdmin):
    form = MemberRoleForm
    fields = ['group', 'name', 'person', 'email']
    raw_id_fields = ['person', 'email']
    list_display = ['person', 'name', 'group']
    search_fields = ['person__name', 'group__name']
    list_filter = ['group', 'name']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(group__in=core.groups()).select_related('group', 'person', 'name')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'group':
            kwargs['queryset'] = core.groups()
        if db_field.name == 'name':
            kwargs['queryset'] = db_field.remote_field.model.objects.filter(slug__in=['chair', 'secr', 'member'])
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        save_group_in_history(obj.group)
        super().save_model(request, obj, form, change)
        GroupEvent.objects.create(group=obj.group, by=core.actor_for(request.user), type='info_changed',
                                  desc=f'Membership updated: {obj.person.name} ({obj.name.name}).')

    def delete_model(self, request, obj):
        group, description = obj.group, f'Membership removed: {obj.person.name} ({obj.name.name}).'
        save_group_in_history(group)
        super().delete_model(request, obj)
        GroupEvent.objects.create(group=group, by=core.actor_for(request.user), type='info_changed', desc=description)


class FgGroupForm(GroupForm):
    def clean(self):
        values = super().clean() or self.cleaned_data
        parent = values.get('parent')
        visited = {self.instance.pk} if self.instance.pk else set()
        while parent:
            if parent.pk in visited:
                self.add_error('parent', 'A group cannot be its own ancestor.')
                break
            visited.add(parent.pk)
            parent = parent.parent
        if self.instance.pk and Group.objects.filter(pk=self.instance.pk, acronym='tida').exists():
            if values.get('acronym') != 'tida':
                self.add_error('acronym', 'Keep the FG-TIDA root identifier.')
            if values.get('parent'):
                self.add_error('parent', 'The focus group is the root of this hierarchy.')
        return values


class DocumentForm(forms.ModelForm):
    document_direction = forms.ChoiceField(required=False, label='Input / output list', choices=[('', 'Neither'), ('input', 'Input document'), ('output', 'Output document')], help_text='Publish a PDF or Word document in the selected document list.')
    document_meeting = forms.ModelChoiceField(required=False, label='Document meeting', queryset=Meeting.objects.none())
    document_source = forms.CharField(required=False, max_length=255, label='Document source', help_text='Organization, author or working group displayed in the list.')
    document_created = forms.SplitDateTimeField(required=False, label='Document creation date', widget=admin.widgets.AdminSplitDateTime, help_text='Leave blank to use the current date for a new list entry.')
    version = forms.CharField(required=False,max_length=32,label='New file version',help_text='Optional X.Y version, e.g. 0.1 or 1.0. Without a value the minor version increases. Archived versions are immutable.')
    catalogued = forms.BooleanField(required=False, initial=True, label='Show in public catalog', help_text='Leave unchecked for repository support files or records awaiting classification. This is catalog inclusion, not confidential storage.')
    upload = forms.FileField(required=False, help_text='Upload a new revision: PDF, DOCX, PPTX, TXT, Markdown or XML (up to 10 MB).')
    lifecycle_state = forms.ModelChoiceField(queryset=State.objects.none(), required=False, label='Document state')
    meeting_session = forms.ModelChoiceField(queryset=Session.objects.none(), required=False, label='Attach to meeting session')
    comment_about_changes = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), label='History note')

    class Meta:
        model = Document
        fields = ['name', 'title', 'type', 'group', 'abstract', 'tags', 'external_url']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['type'].queryset = DocTypeName.objects.filter(slug__in=core.DOCUMENT_TYPES)
        self.fields['document_meeting'].queryset = core.meetings().order_by('-date', '-pk')
        self.fields['type'].initial = 'tida'
        self.fields['group'].queryset = core.groups()
        self.fields['group'].required = True
        self.fields['group'].initial = Group.objects.filter(acronym='tida').first()
        self.fields['tags'].queryset = DocTagName.objects.filter(slug__startswith='tida-').exclude(slug='tida-uncatalogued')
        self.fields['meeting_session'].queryset = Session.objects.filter(meeting__type_id='tida', group__in=core.groups()).select_related('meeting', 'group')
        self.fields['meeting_session'].widget = admin.widgets.AutocompleteSelect(
            SessionPresentation._meta.get_field('session'), admin.site,
            choices=self.fields['meeting_session'].choices)
        type_id = self.data.get('type') or self.instance.type_id or 'tida'
        self.fields['lifecycle_state'].queryset = State.objects.filter(type_id=type_id, used=True)
        if self.instance.pk:
            listing = DocumentListing.objects.filter(document=self.instance).first()
            if listing:
                for field, value in [('document_direction', listing.direction), ('document_meeting', listing.meeting_id), ('document_source', listing.source), ('document_created', listing.created)]:
                    self.fields[field].initial = value
            self.fields['catalogued'].initial = not self.instance.tags.filter(slug='tida-uncatalogued').exists()
            self.fields['lifecycle_state'].initial = self.instance.states.filter(type_id=type_id).first()

    def clean_upload(self):
        upload = self.cleaned_data.get('upload')
        if upload:
            data = upload.read(MAX_BYTES + 1)
            extract_text(data, upload.name)
            upload.seek(0)
        return upload

    def clean_name(self):
        name = self.cleaned_data['name']
        if len(name) > 220:
            raise ValidationError('Use at most 220 characters to leave room for revision filenames.')
        return name

    def clean(self):
        values = super().clean()
        self.instance._fgtida_upload_revision = bool(values.get('upload'))
        if values.get('document_direction'):
            from pathlib import Path
            upload = values.get('upload')
            filename = upload.name if upload else self.instance.uploaded_filename
            if Path(filename or '').suffix.lower() not in {'.pdf', '.docx'}:
                self.add_error('upload', 'Input and output documents require a PDF or Word (.docx) file.')
        if values.get('version'):
            from .versioning import next_version
            if not values.get('upload'):
                self.add_error('version','A version is assigned only when uploading a new file.')
            else:
                try:
                    next_version(self.instance,values['version'])
                except ValidationError as error:
                    self.add_error('version',error)
        if self.instance.pk:
            original = Document.objects.get(pk=self.instance.pk)
            if values.get('name') != original.name:
                self.add_error('name', 'Document identifiers are permanent; create a new document instead.')
            if values.get('type') and values['type'].pk != original.type_id:
                self.add_error('type', 'Keep the document type to preserve revision history and file paths.')
            item=WorkItem.objects.filter(document=original).first()
            selected=values.get('lifecycle_state')
            if item and selected:
                from .work_items import state_of,TRANSITIONS
                current=state_of(original)
                if selected.slug!=current and selected.slug not in TRANSITIONS.get(current,set()):
                    self.add_error('lifecycle_state',f'Cannot move this work item from {current} to {selected.slug}.')
                if selected.slug=='published' and item.repository_id and not item.releases.exists():
                    self.add_error('lifecycle_state','Archive a release before marking the work item Published.')
        session = values.get('meeting_session')
        kind = values.get('type')
        if kind and kind.pk == 'recording':
            if values.get('upload'):
                self.add_error('upload', 'Use the recording URL; large media files are hosted separately.')
            if urlsplit(values.get('external_url', '')).scheme not in ('http', 'https'):
                self.add_error('external_url', 'Enter an HTTP or HTTPS recording URL.')
        if kind and kind.pk in ('agenda', 'minutes') and not session and not (self.instance.pk and self.instance.session_set.exists()):
            self.add_error('meeting_session', 'An agenda or minutes document must belong to a meeting session.')
        if session and self.instance.pk and self.instance.session_set.exclude(meeting=session.meeting).exists():
            self.add_error('meeting_session', 'Keep revisions attached to the original meeting; create a new document for another meeting.')
        if session and values.get('group') and session.group_id != values['group'].pk:
            self.add_error('meeting_session', 'Select a session for the document’s owning group.')
        return values


class DocumentFormatInline(admin.TabularInline):
    model = DocumentFormat
    from .document_formats import DocumentFormatForm, DocumentFormatFormSet
    form = DocumentFormatForm
    formset = DocumentFormatFormSet
    fields = ['revision', 'kind', 'upload', 'download_link']
    readonly_fields = ['revision', 'download_link']
    verbose_name_plural = 'Word / PDF alternatives — new rows attach to the revision saved above'
    extra = 0

    def get_queryset(self, request):
        return super().get_queryset(request).defer('content').order_by('-revision', 'kind')

    def download_link(self, obj):
        if obj and obj.pk:
            return format_html('<a href="{}">{}</a>', reverse('fgtida:document-format', args=[obj.pk]), obj.filename)
        return 'Save to publish this format'

    def has_add_permission(self, request, obj=None):
        return request.user.has_perm('doc.change_document') or request.user.has_perm('doc.add_document')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('doc.change_document')

    has_view_permission = has_change_permission
    has_delete_permission = has_change_permission


class FgDocumentAdmin(DocumentAdmin):
    form = DocumentForm
    fields = ['name', 'title', 'type', 'group', 'abstract', 'catalogued', 'tags', 'lifecycle_state',
              'upload', 'version', 'document_direction', 'document_meeting', 'document_source', 'document_created', 'meeting_session', 'external_url', 'comment_about_changes']
    list_display = ['name', 'title', 'type', 'group', 'public_version', 'time']
    search_fields = ['name', 'title', 'abstract']
    list_filter = ['type', 'group', 'tags']
    raw_id_fields = []
    inlines = [DocAuthorInline, RelatedDocumentInline, AdditionalUrlInLine, DocumentFormatInline]
    actions = []

    def get_queryset(self, request):
        return super().get_queryset(request).filter(group__in=core.groups()).prefetch_related('tida_versions')

    @admin.display(description='Version')
    def public_version(self,obj):
        from .versioning import public_version
        return public_version(obj)

    def save_model(self, request, obj, form, change):
        from .github_catalog import preserve_manual_document
        upload = form.cleaned_data.get('upload')
        state = form.cleaned_data.get('lifecycle_state') or State.objects.filter(type_id=obj.type_id, used=True).order_by('order').first()
        core.save_document(obj, core.actor_for(request.user), form.cleaned_data['comment_about_changes'],
            data=upload.read(MAX_BYTES + 1) if upload else None, filename=upload.name if upload else None, state=state,
            session=form.cleaned_data.get('meeting_session'),version=form.cleaned_data.get('version') or None)
        if change and set(form.changed_data)&{'title','group','abstract','upload','external_url','tags','catalogued'}:
            preserve_manual_document(obj)
        if change and hasattr(obj,'tida_work_item'):
            from .models import GitHubJob
            GitHubJob.objects.get_or_create(kind='state',work_item=obj.tida_work_item,key=f'editor-state:{obj.pk}:{obj.docevent_set.latest("time").pk}')
        session = form.cleaned_data.get('meeting_session')
        if session:
            SessionPresentation.objects.get_or_create(session=session, document=obj)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        values = form.cleaned_data
        if values.get('document_direction'):
            from django.utils import timezone
            existing = DocumentListing.objects.filter(document=form.instance).first()
            DocumentListing.objects.update_or_create(document=form.instance, defaults={
                'direction': values['document_direction'], 'meeting': values.get('document_meeting'),
                'source': values.get('document_source', ''),
                'created': values.get('document_created') or (existing.created if existing else timezone.now())})
        else:
            DocumentListing.objects.filter(document=form.instance).delete()
        tag = DocTagName.objects.get(slug='tida-uncatalogued')
        if form.cleaned_data['catalogued']:
            form.instance.tags.remove(tag)
        else:
            form.instance.tags.add(tag)
        if form.instance.type_id == 'tida-template':
            form.instance.tags.add('tida-templates')
        save_document_in_history(form.instance)


class ContributionRecipientAdmin(admin.ModelAdmin):
    list_display = ['group', 'email', 'active']
    list_filter = ['group', 'active']
    search_fields = ['email', 'group__name']

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission

    def get_queryset(self, request):
        return super().get_queryset(request).filter(group__in=core.groups())

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from .contributions import queue_notifications
        from django.db import transaction
        pending = list(Contribution.objects.filter(group=obj.group, notifications_queued=False).values_list('pk', flat=True)[:100])
        transaction.on_commit(lambda: [queue_notifications(pk) for pk in pending])

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'group':
            kwargs['queryset'] = core.groups()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ContributionRecipientInline(admin.TabularInline):
    model = ContributionRecipient
    extra = 0
    verbose_name_plural = 'Contribution notification recipients (Incubation: FG management)'

    def has_add_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser


class WorkingGroupConfigurationInline(admin.StackedInline):
    model=WorkingGroupConfiguration
    extra=0
    max_num=1
    fields=['number','topic_acronym','github_account']


class IntegrationAdmin(admin.ModelAdmin):
    def has_module_permission(self,request):
        return request.user.is_superuser

    def has_view_permission(self,request,obj=None):
        return request.user.is_superuser

    has_add_permission=has_view_permission
    has_change_permission=has_view_permission
    has_delete_permission=has_view_permission

    def formfield_for_foreignkey(self,db_field,request,**kwargs):
        if db_field.remote_field.model is Document:
            kwargs['queryset']=core.documents(include_uncatalogued=True)
        elif db_field.remote_field.model is Group:
            kwargs['queryset']=core.groups()
        return super().formfield_for_foreignkey(db_field,request,**kwargs)


class GitHubAccountAdmin(IntegrationAdmin):
    list_display=['organization','installation_id','enabled']
    search_fields=['organization']


class WorkingGroupConfigurationAdmin(IntegrationAdmin):
    list_display=['number','group','topic_acronym','github_account']
    search_fields=['group__name','group__acronym']


class GitHubRepositoryAdmin(IntegrationAdmin):
    list_display=['name','account','group','mode','enabled','last_sync','last_error']
    search_fields=['name','account__organization','group__name']
    list_filter=['account','mode','enabled']
    readonly_fields=['last_sync','last_error']
    actions=['queue_sync']

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)
        class EvidenceForm(base):
            category = forms.ChoiceField(required=False, choices=[('', 'Not specified by source')] + core.CATEGORIES)
            def clean(self):
                values = super().clean()
                from .classification import public_github_url
                group = values.get('group')
                if group and group.acronym != 'tida' and not public_github_url(values.get('group_evidence')):
                    self.add_error('group_evidence', 'Provide public GitHub evidence of this group assignment; otherwise select FG-TIDA without a wg assignment.')
                if values.get('category') and not public_github_url(values.get('category_evidence')):
                    self.add_error('category_evidence', 'Provide public GitHub evidence for this category, or leave the category empty.')
                return values
        return EvidenceForm

    @admin.action(description='Queue synchronization for selected repositories')
    def queue_sync(self,request,queryset):
        import uuid
        for repo in queryset.filter(enabled=True):
            GitHubJob.objects.create(kind='catalog',repository=repo,key='admin-sync:'+str(uuid.uuid4()))
        self.message_user(request,'Synchronization queued. The GitHub job command or scheduled job processes it.')


class GitHubSourceAdmin(IntegrationAdmin):
    list_display=['source_key','repository','document','sync_mode','last_sync']
    search_fields=['source_key','repository__name','document__name','document__title']
    list_filter=['sync_mode','repository']
    readonly_fields=['sha','checksum','last_sync','source_url','source_metadata']

    def save_model(self,request,obj,form,change):
        if 'sync_mode' in form.changed_data and obj.sync_mode=='automatic':
            obj.checksum=''
        super().save_model(request,obj,form,change)


class WorkItemAdmin(IntegrationAdmin):
    list_display=['document','repository','sync_mode','working_updated']
    search_fields=['document__title','document__name','repository__name']
    readonly_fields=['working_sha','working_checksum','working_updated','working_manifest']
    fields=['document','repository','short_name','sync_mode','working_sha','working_checksum','working_updated','working_manifest']
    actions=['prepare_repository']

    @admin.action(description='Queue repository creation / metadata pull request')
    def prepare_repository(self,request,queryset):
        import uuid
        for item in queryset:
            GitHubJob.objects.create(kind='provision',work_item=item,key='admin-provision:'+str(uuid.uuid4()))
        self.message_user(request,'Repository setup queued; nothing is pushed directly to main.')


class WorkItemReleaseAdmin(IntegrationAdmin):
    list_display=['work_item','release_version','tag','commit_sha','created']
    search_fields=['work_item__document__name','tag']
    fields=['work_item','release_version','tag','commit_sha','checksum','manifest','created']
    readonly_fields=fields

    @admin.display(description='Version')
    def release_version(self,obj):
        return obj.tag.removeprefix('v')

    def get_queryset(self,request):
        return super().get_queryset(request).defer('source_archive')

    def has_add_permission(self,request):
        return False

    def has_delete_permission(self,request,obj=None):
        return False


class GitHubJobAdmin(IntegrationAdmin):
    list_display=['kind','work_item','repository','created','completed','attempts','last_error']
    readonly_fields=['kind','work_item','repository','key','created','completed','attempts','last_error','available_at']
    actions=['retry_jobs']

    def has_add_permission(self,request):
        return False

    @admin.action(description='Retry selected incomplete jobs')
    def retry_jobs(self,request,queryset):
        from django.utils import timezone
        queryset.filter(completed__isnull=True).update(attempts=0,available_at=timezone.now(),last_error='')
        self.message_user(request,'Incomplete jobs are available for retry.')


class EmailVerificationAdmin(IntegrationAdmin):
    list_display=['user','email','created','verified']
    fields=['user','email','created','verified']
    readonly_fields=fields
    search_fields=['email','user__username']

    def has_add_permission(self,request):
        return False

    def has_delete_permission(self, request, obj=None):
        return can_delete_account_credential(request, obj)


class FgGroupAdmin(GroupAdmin):
    form = FgGroupForm
    inlines = [MemberRoleInline, GroupURLInline, ContributionRecipientInline,WorkingGroupConfigurationInline]
    fields = ['acronym', 'name', 'type', 'state', 'parent', 'description', 'charter',
              'list_email', 'list_subscribe', 'list_archive']
    raw_id_fields = ['charter']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(pk__in=core.groups().values('pk'))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'parent':
            kwargs['queryset'] = core.groups()
            kwargs['initial'] = Group.objects.filter(acronym='tida').first()
        elif db_field.name == 'type':
            kwargs['queryset'] = db_field.remote_field.model.objects.filter(slug__in=['fg', 'wg', 'team'])
        elif db_field.name == 'charter':
            kwargs['queryset'] = core.documents().filter(type_id='charter')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if change:
            save_group_in_history(Group.objects.get(pk=obj.pk))
        if obj.acronym != 'tida' and obj.parent is None:
            obj.parent = Group.objects.get(acronym='tida')
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        from .contributions import queue_notifications
        from django.db import transaction
        pending = list(Contribution.objects.filter(group=form.instance, notifications_queued=False).values_list('pk', flat=True)[:100])
        transaction.on_commit(lambda: [queue_notifications(pk) for pk in pending])
        GroupEvent.objects.create(group=form.instance, by=core.actor_for(request.user), type='info_changed',
                                  desc='Group information, resources or roles updated through administration.')


class FloorPlanForm(forms.ModelForm):
    def clean_image(self):
        from pathlib import Path
        upload = self.cleaned_data.get('image')
        if not upload or 'image' not in self.changed_data:
            return upload
        if upload.size > MAX_BYTES:
            raise ValidationError('Use a PDF, PNG or JPEG up to 10 MB.')
        suffix = Path(upload.name).suffix.lower()
        try:
            if suffix == '.pdf':
                extract_text(upload.read(MAX_BYTES + 1), upload.name)
            elif suffix in {'.png', '.jpg', '.jpeg'}:
                from PIL import Image
                Image.open(upload).verify()
            else:
                raise ValidationError('Use PDF, PNG or JPEG.')
        except ValidationError:
            raise
        except Exception as error:
            raise ValidationError('Cannot read this floor plan file.') from error
        finally:
            upload.seek(0)
        return upload


class FgMeetingForm(forms.ModelForm):
    number = forms.SlugField(max_length=64, label='Meeting URL identifier',
        help_text='Use a short unique identifier with letters, numbers, hyphens or underscores, for example tida-2026-11-04. This is used in meeting links, not a full meeting title.')

    class Meta:
        model = Meeting
        fields = '__all__'


class FgMeetingAdmin(MeetingAdmin):
    form = FgMeetingForm
    search_fields = ['title', 'number']
    list_display = ['display_title', 'number', 'category', 'date', 'location', 'time_zone']
    list_filter = ['category']
    list_select_related = ['category']
    fields = ['title', 'number', 'category', 'date', 'days', 'city', 'country', 'time_zone',
              'venue_name', 'venue_addr', 'agenda_info_note', 'agenda_warning_note', 'proceedings_final', 'manage_agenda']
    readonly_fields = ['manage_agenda']
    actions = ['cancel_sessions']

    @admin.display(description='Agenda and session materials')
    def manage_agenda(self, obj):
        if not obj or not obj.pk:
            return 'Save this meeting, then add agenda sessions here.'
        links = format_html('<a href="{}?meeting={}">Add agenda session</a> · <a href="{}?meeting__id__exact={}">Edit sessions and materials</a>',
            reverse('admin:meeting_session_add'), obj.pk, reverse('admin:meeting_session_changelist'), obj.pk)
        # Invalid POST data can be copied to the instance before Django renders
        # the form errors. Never reverse a URL using that unvalidated value.
        try:
            agenda_url = reverse('fgtida:meeting', args=[obj.number])
        except NoReverseMatch:
            return format_html('{} · Correct the meeting URL identifier to view the agenda.', links)
        return format_html('{} · <a href="{}">View agenda</a>', links, agenda_url)

    @admin.action(description='Cancel all sessions of selected meetings (retain history)', permissions=['change'])
    def cancel_sessions(self, request, queryset):
        count = 0
        for session in Session.objects.filter(meeting__in=queryset):
            workflow.set_session_status(session, 'canceled', core.actor_for(request.user))
            count += 1
        self.message_user(request, f'{count} sessions marked cancelled. No announcement emails were sent.')

    def get_queryset(self, request):
        return super().get_queryset(request).filter(type_id='tida')

    def save_model(self, request, obj, form, change):
        obj.type_id = 'tida'
        super().save_model(request, obj, form, change)
        if obj.schedule_id is None:
            obj.schedule = Schedule.objects.create(meeting=obj, name='official', owner=core.actor_for(request.user), public=True, visible=True)
            obj.save(update_fields=['schedule'])


class FgImportantDateNameForm(forms.ModelForm):
    slug = forms.SlugField(max_length=32, label='Identifier',
        help_text='Use a short identifier. The tracker adds the tida- prefix if needed.')

    class Meta:
        model = ImportantDateName
        fields = '__all__'

    def clean_slug(self):
        slug = self.cleaned_data['slug'].lower()
        if not slug.startswith('tida-'):
            slug = 'tida-' + slug
        if len(slug) > 32:
            raise ValidationError('Use at most 27 characters after the tida- prefix.')
        return slug


class FgImportantDateNameAdmin(ImportantDateNameAdmin):
    form = FgImportantDateNameForm

    def get_queryset(self, request):
        return super().get_queryset(request).filter(slug__startswith='tida-')

    def get_readonly_fields(self, request, obj=None):
        return ('slug',) if obj else ()


class AssignmentInline(admin.TabularInline):
    model = SchedTimeSessAssignment
    extra = 0

    def get_formset(self, request, obj=None, **kwargs):
        request._fgtida_meeting = obj.meeting_id if obj else request.POST.get('meeting') or request.GET.get('meeting')
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        meeting_id = getattr(request, '_fgtida_meeting', None)
        if db_field.name == 'schedule':
            kwargs['queryset'] = Schedule.objects.filter(meeting__type_id='tida', meeting_id=meeting_id)
        elif db_field.name == 'timeslot':
            kwargs['queryset'] = TimeSlot.objects.filter(meeting__type_id='tida', meeting_id=meeting_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class FgSessionForm(forms.ModelForm):
    agenda_start = forms.CharField(required=False, label='Agenda date and start time',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        help_text='Enter the time in the meeting’s timezone. Saving creates the schedule entry automatically. Leave blank for an unscheduled session.')
    agenda_minutes = forms.IntegerField(required=False, min_value=1, max_value=1440, label='Duration (minutes)')
    agenda_room = forms.ModelChoiceField(required=False, queryset=Room.objects.none(), label='Location', help_text='Optional. Add rooms under Meetings → Rooms.')
    scheduling_status = forms.ChoiceField(required=False, choices=[('', 'Status not recorded')] +
        [(slug, label) for slug, (label, color) in workflow.SESSION_STATES.items() if slug != 'deleted'],
        help_text='Records the session workflow state. Announced means the editor confirms publication; this does not send email. Previous states remain in history.')

    class Meta:
        model = Session
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        meeting_id = self.data.get('meeting') or self.initial.get('meeting') or self.instance.meeting_id
        self.fields['agenda_room'].queryset = Room.objects.filter(meeting_id=meeting_id, meeting__type_id='tida')
        if self.instance.pk:
            from zoneinfo import ZoneInfo
            assignment = self.instance.timeslotassignments.filter(schedule_id=self.instance.meeting.schedule_id).select_related('timeslot').first()
            if assignment:
                slot = assignment.timeslot
                self.fields['agenda_start'].initial = slot.time.astimezone(ZoneInfo(self.instance.meeting.time_zone or 'UTC')).strftime('%Y-%m-%dT%H:%M')
                self.fields['agenda_minutes'].initial = int(slot.duration.total_seconds() / 60)
                self.fields['agenda_room'].initial = slot.location_id
        if self.instance.pk:
            self.fields['scheduling_status'].initial = Session.objects.with_current_status().get(pk=self.instance.pk).current_status

    def clean(self):
        from datetime import datetime, timedelta, timezone as dt_timezone
        from zoneinfo import ZoneInfo
        values = super().clean()
        meeting = values.get('meeting')
        if self.instance.pk and meeting and meeting.pk != self.instance.meeting_id:
            self.add_error('meeting', 'Create a new session to move to another meeting; existing materials and schedules belong to this meeting.')
        if values.get('agenda_start') and meeting:
            try:
                start = datetime.fromisoformat(values['agenda_start'])
                if start.tzinfo is not None:
                    raise ValueError()
                zone = ZoneInfo(meeting.time_zone or 'UTC')
                aware = start.replace(tzinfo=zone)
                if aware.astimezone(dt_timezone.utc).astimezone(zone).replace(tzinfo=None) != start or aware.utcoffset() != start.replace(tzinfo=zone, fold=1).utcoffset():
                    raise ValueError()
                if not meeting.date <= start.date() < meeting.date + timedelta(days=max(meeting.days, 1)):
                    self.add_error('agenda_start', 'Choose a date within the meeting dates.')
                values['agenda_datetime'] = aware
            except ValueError:
                self.add_error('agenda_start', 'Enter a valid, unambiguous local meeting time.')
            if not values.get('agenda_minutes'):
                self.add_error('agenda_minutes', 'Enter the session duration.')
            if self.instance.pk and self.instance.timeslotassignments.filter(schedule_id=meeting.schedule_id).count() > 1:
                self.add_error('agenda_start', 'This session has multiple schedule entries. Clear this field and use the assignment rows below to edit them.')
        return values


class SchedulingHistoryInline(admin.TabularInline):
    model = SchedulingEvent
    fields = ['time', 'status', 'by']
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class FgSessionAdmin(SessionAdmin):
    form = FgSessionForm
    fields = ['meeting', 'name', 'short', 'group', 'purpose', 'type', 'scheduling_status',
              'agenda_start', 'agenda_minutes', 'agenda_room', 'agenda_note', 'remote_instructions', 'on_agenda', 'manage_materials']
    readonly_fields = ['manage_materials']
    raw_id_fields = []
    inlines = [AssignmentInline, SchedulingHistoryInline, SessionResourceInline]

    @admin.display(description='Documents and materials')
    def manage_materials(self, obj):
        if not obj or not obj.pk:
            return 'Save this session, then upload its agenda, minutes or slides here. Add recording and participation links below.'
        return format_html('<a href="{}?type=agenda&amp;meeting_session={}&amp;group={}">Upload agenda document</a> · <a href="{}?meeting_session={}&amp;group={}">Add other document</a>',
            reverse('admin:doc_document_add'), obj.pk, obj.group_id, reverse('admin:doc_document_add'), obj.pk, obj.group_id)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if form.cleaned_data.get('agenda_datetime'):
            from datetime import timedelta
            meeting = obj.meeting
            if not meeting.schedule_id:
                meeting.schedule = Schedule.objects.create(meeting=meeting, name='official', owner=core.actor_for(request.user), public=True, visible=True)
                meeting.save(update_fields=['schedule'])
            assignment = obj.timeslotassignments.filter(schedule=meeting.schedule).select_related('timeslot').first()
            # Never change another session's shared time slot.
            slot = assignment.timeslot if assignment and not assignment.timeslot.sessionassignments.exclude(pk=assignment.pk).exists() else TimeSlot(meeting=meeting, type_id='session')
            slot.name = obj.name or obj.group.name
            slot.time = form.cleaned_data['agenda_datetime']
            slot.duration = timedelta(minutes=form.cleaned_data['agenda_minutes'])
            slot.location = form.cleaned_data['agenda_room']
            slot.save()
            if assignment:
                assignment.timeslot = slot
                assignment.save(update_fields=['timeslot'])
            else:
                SchedTimeSessAssignment.objects.create(session=obj, schedule=meeting.schedule, timeslot=slot)
        if form.cleaned_data.get('scheduling_status'):
            workflow.set_session_status(obj, form.cleaned_data['scheduling_status'], core.actor_for(request.user))

    def get_queryset(self, request):
        return super().get_queryset(request).filter(meeting__type_id='tida')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'meeting':
            kwargs['queryset'] = core.meetings()
        elif db_field.name == 'group':
            kwargs['queryset'] = core.groups()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ScopedNativeAdmin:
    """Keep auxiliary native admin lists and relationship choices in FG-TIDA."""
    scope = None

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(group__in=core.groups()) if self.scope == 'groups' else qs.filter(**self.scope)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.remote_field.model == Document:
            kwargs['queryset'] = core.documents()
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)

        class ConsistentMeetingForm(base):
            def clean(self):
                values = super().clean()
                meeting_ids = set()
                for name in ('meeting', 'session', 'schedule', 'timeslot', 'location'):
                    value = values.get(name)
                    meeting_id = value.pk if isinstance(value, Meeting) else getattr(value, 'meeting_id', None)
                    if meeting_id:
                        meeting_ids.add(meeting_id)
                if len(meeting_ids) > 1:
                    raise ValidationError('Session, schedule, time slot and room must belong to the same meeting.')
                document, session = values.get('document'), values.get('session')
                if document and session and document.group_id != session.group_id:
                    raise ValidationError('The document and session must belong to the same working group.')
                return values

        return ConsistentMeetingForm

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        related = db_field.remote_field.model
        if self.model._meta.label_lower == 'meeting.importantdate' and db_field.name == 'name':
            kwargs['queryset'] = related.objects.filter(slug__startswith='tida-')
        if related == Meeting:
            kwargs['queryset'] = core.meetings()
        elif related == Group:
            kwargs['queryset'] = core.groups()
        elif related == Document:
            kwargs['queryset'] = core.documents()
        elif related == Session:
            kwargs['queryset'] = Session.objects.filter(meeting__type_id='tida')
        elif related in (Schedule, TimeSlot):
            kwargs['queryset'] = related.objects.filter(meeting__type_id='tida')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


def hidden_admin_permission(request, obj=None):
    return False


def configure_admin():
    if not admin.site.is_registered(SecurityQuestion):
        admin.site.register(SecurityQuestion, SecurityQuestionAdmin)
    for model, cls in [(User, FgUserAdmin), (Person, FgPersonAdmin), (Contribution, ContributionAdmin), (ContributionRecipient, ContributionRecipientAdmin), (SessionResource, SessionResourceAdmin), (OutgoingMail, OutgoingMailAdmin),
        (GitHubAccount,GitHubAccountAdmin),(WorkingGroupConfiguration,WorkingGroupConfigurationAdmin),
        (GitHubRepository,GitHubRepositoryAdmin),(GitHubSource,GitHubSourceAdmin),(WorkItem,WorkItemAdmin),
        (WorkItemRelease,WorkItemReleaseAdmin),(GitHubJob,GitHubJobAdmin),(EmailVerification,EmailVerificationAdmin)]:
        if model in admin.site._registry:
            admin.site.unregister(model)
        admin.site.register(model, cls)
    admin.site.site_header = 'FG-TIDA Datatracker administration'
    admin.site.site_title = 'FG-TIDA administration'
    admin.site.index_title = 'Documents, groups, meetings and participants'
    for model, cls in [(Document, FgDocumentAdmin), (Group, FgGroupAdmin), (Role, FgRoleAdmin), (Meeting, FgMeetingAdmin), (Session, FgSessionAdmin), (ImportantDateName, FgImportantDateNameAdmin), (MeetingCategory, admin.ModelAdmin)]:
        if model in admin.site._registry:
            admin.site.unregister(model)
        admin.site.register(model, cls)
    scopes = {
        'group.groupmilestone': 'groups',
        'group.groupurl': 'groups',
        'meeting.timeslot': {'meeting__type_id': 'tida'},
        'meeting.room': {'meeting__type_id': 'tida'},
        'meeting.floorplan': {'meeting__type_id': 'tida'},
        'meeting.schedule': {'meeting__type_id': 'tida'},
        'meeting.importantdate': {'meeting__type_id': 'tida'},
        'meeting.registration': {'meeting__type_id': 'tida'},
        'meeting.sessionpresentation': {'session__meeting__type_id': 'tida'},
        'meeting.schedtimesessassignment': {'schedule__meeting__type_id': 'tida'},
    }
    for model, registered in list(admin.site._registry.items()):
        scope = scopes.get(model._meta.label_lower)
        if scope:
            if model._meta.label_lower == 'meeting.floorplan':
                registered.form = FloorPlanForm
            cls = type('Fg' + registered.__class__.__name__, (ScopedNativeAdmin, registered.__class__), {'scope': scope})
            if model._meta.label_lower == 'meeting.floorplan':
                cls.form = FloorPlanForm
            admin.site.unregister(model)
            admin.site.register(model, cls)
    # Preserve upstream URL names for template reversals. Deny hidden models at
    # the admin permission layer too, so related widgets don't advertise their
    # add/change/delete/view popups and lead editors to a blocked route.
    from .security import ADMIN_MODELS
    allowed = ADMIN_MODELS
    # Keep lists and relationship pickers usable as meetings, people and
    # documents grow. Autocomplete uses the scoped target admin queryset.
    searchable = {
        'meeting.floorplan': ['name', 'meeting__number', 'meeting__title'],
        'meeting.room': ['name', 'meeting__number'],
        'meeting.timeslot': ['name', 'meeting__number'],
        'meeting.importantdate': ['meeting__number', 'name__name'],
        'meeting.sessionpresentation': ['session__name', 'document__name', 'document__title'],
        'meeting.schedtimesessassignment': ['session__name', 'schedule__meeting__number'],
    }
    for model, registered in admin.site._registry.items():
        opts = model._meta
        if opts.model_name not in allowed.get(opts.app_label, set()):
            registered.has_add_permission = hidden_admin_permission
            registered.has_change_permission = hidden_admin_permission
            registered.has_delete_permission = (can_delete_account_credential
                if opts.label_lower == 'otp_totp.totpdevice' else hidden_admin_permission)
            registered.has_view_permission = hidden_admin_permission
            continue
        registered.list_per_page = 50
        registered.list_max_show_all = 0
        registered.show_full_result_count = False
        if opts.label_lower in searchable:
            registered.search_fields = searchable[opts.label_lower]
    for model, registered in admin.site._registry.items():
        opts = model._meta
        if opts.model_name not in allowed.get(opts.app_label, set()):
            continue
        candidates = []
        for field in opts.fields:
            if not field.many_to_one:
                continue
            related = field.remote_field.model
            target = admin.site._registry.get(related)
            if target and target.search_fields and related._meta.model_name in allowed.get(related._meta.app_label, set()):
                candidates.append(field.name)
        registered.autocomplete_fields = candidates
        registered.raw_id_fields = [name for name in registered.raw_id_fields if name not in candidates]
    original = admin.site.get_app_list

    def app_list(site, request, app_label=None):
        apps = original(request, app_label)
        for app in apps:
            app['models'] = [model for model in app['models'] if model['object_name'].lower() in allowed.get(app['app_label'], set())]
        return [app for app in apps if app['models']]

    admin.site.get_app_list = MethodType(app_list, admin.site)
