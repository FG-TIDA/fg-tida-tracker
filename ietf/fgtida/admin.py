"""Personalize upstream admins without replacing their database models."""
from types import MethodType
from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
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
from .models import Contribution, ContributionRecipient, ContributionSequence, SessionResource, OutgoingMail, AdministratorApproval, SecurityQuestion
from .models import (EmailVerification, GitHubAccount, WorkingGroupConfiguration, GitHubRepository,
    GitHubSource, WorkItem, WorkItemRelease, GitHubJob, DocumentAttribution, DocumentListing, DocumentFormat,
    PublicDocumentType)
from .models import FileScan, FileScanEvent, FileScanException
from .contributions import ContributionForm
from .resource_files import SessionResourceForm
from .training import TrainingVideoAdmin
from .models import TrainingCategory, TrainingVideo
from django.contrib.auth.models import User
from ietf.ietfauth.admin import CustomUserAdmin
from django.utils.html import format_html
from django.utils import timezone
from django.urls import reverse, NoReverseMatch
from django.shortcuts import redirect
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
    meeting = forms.ModelChoiceField(queryset=core.meetings(), required=False)
    verify_conversion = forms.BooleanField(required=False,
        label='I compared the converted RST text, figures and package with the Word original and confirm they preserve the contribution.')
    decision = forms.ChoiceField(required=False, choices=[('','Save review note'),('adopt','Adopt into a working group'),('reject','Reject proposal')])
    adopt_into = forms.ModelChoiceField(queryset=Group.objects.filter(parent__acronym='tida',type_id='wg').exclude(state_id='conclude'),required=False,
        help_text='For an Incubation submission, create the new WG and its numbered GitHub configuration first, then select it here.')

    class Meta:
        model=Contribution
        fields=['sequence_number', 'review_note', 'contact_name', 'contact_email', 'contact_affiliation', 'meeting']

    def clean_meeting(self):
        if 'meeting' not in self.data and self.instance.pk:
            return self.instance.meeting
        return self.cleaned_data.get('meeting')

    def clean_sequence_number(self):
        number = self.cleaned_data['sequence_number']
        if 'sequence_number' in self.changed_data:
            prefix = self.instance.number_prefix or self.instance.submission_prefix()
            self.numbering_sequence = ContributionSequence.lock(prefix)
            records = Contribution.objects.filter(number_prefix=prefix)
            if not records.filter(pk=self.instance.pk).exists():
                raise ValidationError('This work item is no longer in the list.')
            count = records.count()
            if number is None or not 1 <= number <= count:
                raise ValidationError(f'Choose a position from 1 to {count}.')
        return number

    def clean(self):
        values=super().clean()
        if values.get('decision')=='adopt' and self.instance.group.acronym=='incubation' and not values.get('adopt_into'):
            self.add_error('adopt_into','Select the working group formed to adopt this proposal.')
        if values.get('decision') and self.instance.status!='pending':
            self.add_error('decision','This proposal already has a review decision. Use its document lifecycle for further changes.')
        if values.get('verify_conversion'):
            from .file_safety import allowed
            if self.instance.submission_method != 'word' or not self.instance.source_archive:
                self.add_error('verify_conversion', 'Convert the Word submission first, then check its source preview.')
            elif not allowed(bytes(self.instance.content), self.instance.filename) or not allowed(bytes(self.instance.source_archive), 'source.zip'):
                self.add_error('verify_conversion', 'The original and source package must pass security checks or have an authorized exception.')
        if values.get('decision') == 'adopt' and self.instance.submission_method == 'word' and not self.instance.conversion_verified_at and not values.get('verify_conversion'):
            self.add_error('decision', 'The contributor or administrator must verify the automatic Word-to-RST conversion before adoption.')
        return values


class AdminContributionForm(ContributionForm):
    class Meta(ContributionForm.Meta):
        fields = ['submitter', 'contact_name', 'contact_email', 'contact_affiliation', *ContributionForm.Meta.fields]
        widgets = {'submitter': admin.widgets.ForeignKeyRawIdWidget(Contribution._meta.get_field('submitter').remote_field, admin.site)}

    def clean(self):
        values = super().clean()
        if not values.get('submitter') and not values.get('contact_name'):
            self.add_error('contact_name', 'Enter the contributor name when no platform account is selected.')
        return values


class ContributionAdmin(admin.ModelAdmin):
    form=ContributionReviewForm
    list_display = ['reference', 'title', 'submitter', 'group', 'created', 'status', 'notifications_queued']
    list_filter = ['status', 'group', 'meeting']
    search_fields = ['title', 'submitter__username', 'contact_name', 'contact_email', 'contact_affiliation', 'meeting__number', 'meeting__title']
    fields = ['reference', 'sequence_number', 'title', 'submitter', 'contact_name', 'contact_email', 'contact_affiliation', 'group', 'meeting', 'abstract', 'submitted_file', 'source_details', 'submission_method', 'source_commit',
              'source_folder', 'entry_document', 'conversion_verified_at', 'conversion_verified_by', 'verify_conversion', 'created',
              'status', 'review_note', 'decision', 'adopt_into', 'draft_repository_url', 'reviewed_by', 'reviewed_at', 'document', 'notifications_queued']
    readonly_fields = [field for field in fields if field not in {'sequence_number','review_note','decision','adopt_into', 'verify_conversion', 'contact_name', 'contact_email', 'contact_affiliation', 'meeting'}]
    actions = ['accept_contributions', 'reject_contributions', 'convert_word_contributions', 'rebuild_word_packages']
    ordering = ['number_prefix', 'sequence_number', 'pk']

    def get_fields(self, request, obj=None):
        if obj is None:
            return [*AdminContributionForm.Meta.fields, 'upload', 'publication_consent']
        return super().get_fields(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if not obj:
            return []
        fields = list(self.readonly_fields)
        if not request.user.has_perm('doc.change_document'):
            fields += ['contact_name', 'contact_email', 'contact_affiliation', 'meeting']
        return fields

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs['form'] = AdminContributionForm
        return super().get_form(request, obj, **kwargs)

    def save_model(self,request,obj,form,change):
        if not change:
            from .contributions import record_submission
            record_submission(obj, form, request.user)
            return
        if 'sequence_number' in form.changed_data:
            sequence = form.numbering_sequence
            sequence.reorder(moved=obj, position=obj.sequence_number)
        if form.cleaned_data.get('verify_conversion') and not obj.conversion_verified_at:
            from django.utils import timezone
            obj.conversion_verified_at = timezone.now()
            obj.conversion_verified_by = request.user
        obj.save(update_fields=['review_note', 'contact_name', 'contact_email', 'contact_affiliation',
            'conversion_verified_at', 'conversion_verified_by', 'meeting'])
        if any(field in form.changed_data for field in ('contact_name', 'contact_email', 'contact_affiliation')):
            from .contributions import sync_contact_metadata
            sync_contact_metadata(obj, clear=True, previous=form.initial)
        from .contributions import accept,reject
        if form.cleaned_data.get('decision')=='adopt':
            if not request.user.has_perm('doc.add_document'):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied
            accept(obj.pk,request.user,form.cleaned_data.get('adopt_into'))
        elif form.cleaned_data.get('decision')=='reject':
            reject(obj.pk,request.user)

    @admin.display(description='Contribution number')
    def reference(self, obj):
        return obj.reference

    def get_search_results(self, request, queryset, search_term):
        number = Contribution.reference_parts(search_term)
        if number:
            return queryset.filter(number_prefix=number[0], sequence_number=number[1]), False
        return super().get_search_results(request, queryset, search_term)

    def get_queryset(self, request):
        return super().get_queryset(request).defer('content', 'source_archive').select_related('submitter', 'group')

    def has_add_permission(self, request):
        return request.user.has_perm('doc.add_document') and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and (
            not obj or not obj.document_id or request.user.has_perm('doc.delete_document'))

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    def delete_view(self, request, object_id, extra_context=None):
        obj = self.get_object(request, object_id)
        if obj and obj.document_id:
            if not self.has_delete_permission(request, obj):
                raise PermissionDenied
            return redirect('admin:doc_document_delete', obj.document_id)
        return super().delete_view(request, object_id, extra_context)

    def submitted_file(self, obj):
        return format_html('<a href="{}">{}</a>', reverse('fgtida:contribution-download', args=[obj.pk]), obj.filename)

    def source_details(self, obj):
        if obj.document_id:
            return format_html('<a href="{}">Review submission or archive a new version</a> · <a href="{}">Manage the public work item</a>',
                reverse('fgtida:contribution', args=[obj.pk]), reverse('admin:doc_document_change', args=[obj.document_id]))
        return format_html('<a href="{}">Review submission</a>', reverse('fgtida:contribution', args=[obj.pk]))

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

    @admin.action(description='Convert selected Word contributions to RST ZIP packages', permissions=['change'])
    def convert_word_contributions(self, request, queryset):
        if not request.user.has_perm('doc.change_document'):
            raise PermissionDenied
        from .contributions import convert_existing_word
        converted = 0
        for item in queryset[:10]:
            try:
                changed = convert_existing_word(item.pk, request.user)
            except ValidationError as error:
                self.message_user(request, f'{item.reference}: ' + '; '.join(error.messages), level='error')
            else:
                if changed:
                    converted += 1
                    self.log_change(request, item, 'Word converted to RST ZIP; original retained and verification required.')
        self.message_user(request, f'Converted {converted} contribution(s). Numbers and work-item IDs were preserved. Check each conversion before marking it verified. No notifications were sent.')

    @admin.action(description='Rebuild Word packages with the specification template', permissions=['change'])
    def rebuild_word_packages(self, request, queryset):
        if not request.user.has_perm('doc.change_document'):
            raise PermissionDenied
        from django.template.response import TemplateResponse
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
        confirmed = request.POST.get('confirm_rebuild') == 'yes'
        class RebuildForm(forms.Form):
            reason = forms.CharField(min_length=10, max_length=400, widget=forms.Textarea(attrs={'rows': 3}))
            acknowledge = forms.BooleanField(label='I understand this regenerates content from the original Word file, archives a new package version and resets conversion verification.')
        form = RebuildForm(request.POST if confirmed else None)
        if not confirmed or not form.is_valid():
            return TemplateResponse(request, 'fgtida/admin/rebuild_word_packages.html', {
                **self.admin_site.each_context(request), 'title': 'Rebuild Word source packages',
                'form': form, 'records': queryset[:10], 'action_checkbox_name': ACTION_CHECKBOX_NAME})
        from .contributions import convert_existing_word
        count = 0
        for item in queryset[:10]:
            try:
                changed = convert_existing_word(item.pk, request.user, rebuild=True, reason=form.cleaned_data['reason'])
            except ValidationError as error:
                self.message_user(request, f'{item.reference}: ' + '; '.join(error.messages), level='error')
            else:
                if changed:
                    count += 1
                    self.log_change(request, item, 'Word source package rebuilt: ' + form.cleaned_data['reason'])
        self.message_user(request, f'Rebuilt {count} package(s). Earlier archives were preserved; review the new conversion before verifying it. No notifications were sent.')


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['person'].help_text = 'Select a People profile. A platform user account is optional.'
        self.fields['email'].help_text = 'Select a contact address saved on the selected People profile.'

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
            kwargs['queryset'] = db_field.remote_field.model.objects.filter(slug__in=core.GROUP_ROLES)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class FgRoleAdmin(admin.ModelAdmin):
    form = MemberRoleForm
    fields = ['group', 'name', 'person', 'email']
    raw_id_fields = ['person', 'email']
    list_display = ['person', 'name', 'group']
    search_fields = ['person__name', 'group__name']
    list_filter = ['group', 'name']

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if not str(initial.get('group', '')).isdigit() or not core.groups().filter(pk=initial['group']).exists():
            initial.pop('group', None)
        return initial

    def get_queryset(self, request):
        return super().get_queryset(request).filter(group__in=core.groups()).select_related('group', 'person', 'name')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'group':
            kwargs['queryset'] = core.groups()
        if db_field.name == 'name':
            kwargs['queryset'] = db_field.remote_field.model.objects.filter(slug__in=core.GROUP_ROLES)
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
    current_version = forms.CharField(required=False, max_length=32, label='Current archive version',
        help_text='Correct the X.Y label of the current archive. Existing links continue to work. GitHub release versions follow their release tags.')
    document_direction = forms.ChoiceField(required=False, label='Liaison statements', choices=[('', 'Neither'), ('input', 'Incoming liaison statement'), ('output', 'Outgoing liaison statement')], help_text='Preserve the received/sent Word, PDF or ZIP as the original. ZIPs may contain Word/PDF documents and supporting text/images. An optional PDF reading copy can be attached to the same revision.')
    document_meeting = forms.ModelChoiceField(required=False, label='Document meeting', queryset=Meeting.objects.none())
    document_source = forms.CharField(required=False, max_length=255, label='Document source', help_text='Organization, author or working group displayed in the list.')
    document_created = forms.SplitDateTimeField(required=False, label='Document creation date', widget=admin.widgets.AdminSplitDateTime, help_text='Leave blank to use the current date for a new list entry.')
    version = forms.CharField(required=False,max_length=32,label='New file version',help_text='Optional X.Y version. Otherwise use the version declared in the source, or start at 0.0 and increase on subsequent uploads.')
    catalogued = forms.BooleanField(required=False, initial=True, label='Show in public catalog', help_text='Leave unchecked for repository support files or records awaiting classification. This is catalog inclusion, not confidential storage.')
    upload = forms.FileField(required=False, help_text='Upload a new revision: RST source ZIP, RST, PDF, DOCX, PPTX, TXT, Markdown or XML (up to 10 MB).')
    lifecycle_state = forms.ModelChoiceField(queryset=State.objects.none(), required=False, label='Document state')
    meeting_session = forms.ModelChoiceField(queryset=Session.objects.none(), required=False, label='Attach to meeting session')
    comment_about_changes = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), label='History note')

    class Meta:
        model = Document
        fields = ['name', 'title', 'type', 'group', 'abstract', 'external_url']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['type'].queryset = DocTypeName.objects.filter(slug__in=core.DOCUMENT_TYPES)
        self.fields['type'].label = 'Workflow type'
        self.fields['type'].help_text = 'Controls lifecycle states and file storage. Use Public metadata below to change the type readers see.'
        self.fields['document_meeting'].queryset = core.meetings().order_by('-date', '-pk')
        self.fields['type'].initial = 'tida'
        self.fields['group'].queryset = core.groups()
        self.fields['group'].required = True
        self.fields['group'].initial = Group.objects.filter(acronym='tida').first()
        self.fields['meeting_session'].queryset = Session.objects.filter(meeting__type_id='tida', group__in=core.groups()).select_related('meeting', 'group')
        self.fields['meeting_session'].widget = admin.widgets.AutocompleteSelect(
            SessionPresentation._meta.get_field('session'), admin.site,
            choices=self.fields['meeting_session'].choices)
        type_id = self.data.get('type') or self.instance.type_id or 'tida'
        self.fields['lifecycle_state'].queryset = State.objects.filter(type_id=type_id, used=True)
        if self.instance.pk:
            from .models import DocumentVersion
            self.current_version_record = DocumentVersion.objects.filter(document=self.instance, revision=self.instance.rev).first()
            if self.current_version_record:
                self.fields['current_version'].initial = self.current_version_record.version
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
        upload = values.get('upload')
        if upload and not values.get('version'):
            from .versioning import declared_version, next_version
            try:
                detected = declared_version(extract_text(upload.read(MAX_BYTES + 1), upload.name))
                next_version(self.instance, detected)
            except ValidationError as error:
                self.add_error('version', error)
            finally:
                upload.seek(0)
        current = getattr(self, 'current_version_record', None)
        if values.get('current_version') and (not current or values['current_version'] != current.version):
            if not current:
                self.add_error('current_version', 'Upload the first archive before setting its version.')
            elif values.get('upload'):
                self.add_error('current_version', 'Save the current version correction before uploading a new revision.')
            else:
                from .versioning import validate_correction
                try:
                    validate_correction(current, values['current_version'])
                except ValidationError as error:
                    self.add_error('current_version', error)
        if values.get('document_direction'):
            from pathlib import Path
            upload = values.get('upload')
            filename = upload.name if upload else self.instance.uploaded_filename
            if Path(filename or '').suffix.lower() not in {'.pdf', '.docx', '.rst', '.zip'}:
                self.add_error('upload', 'Input and output documents require an RST source ZIP, RST, PDF or Word (.docx) file.')
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


class PublicDocumentMetadataForm(forms.ModelForm):
    def clean_authors(self):
        from .credits import clean_values
        return '\n'.join(clean_values(self.cleaned_data['authors'], 'authors'))

    def save(self, commit=True):
        from .credits import record_editor_changes
        record_editor_changes(self.instance, self.changed_data)
        return super().save(commit=commit)

    def clean_contact_emails(self):
        from django.core.validators import validate_email
        addresses = list(dict.fromkeys(address.strip() for address in self.cleaned_data['contact_emails'].splitlines() if address.strip()))
        for address in addresses:
            validate_email(address)
        return '\n'.join(addresses)


class DocumentAttributionInline(admin.StackedInline):
    model = DocumentAttribution
    form = PublicDocumentMetadataForm
    verbose_name = 'Public metadata'
    verbose_name_plural = 'Public metadata'
    extra = 1
    max_num = 1

    def has_add_permission(self, request, obj=None):
        return request.user.has_perm('doc.change_document') or request.user.has_perm('doc.add_document')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('doc.change_document')

    has_view_permission = has_change_permission
    has_delete_permission = has_change_permission


class PublicDocumentTypeAdmin(admin.ModelAdmin):
    search_fields = ['name']

    def get_readonly_fields(self, request, obj=None):
        return ['name'] if obj and obj.name == PublicDocumentType.CONTRIBUTION else []

    def has_delete_permission(self, request, obj=None):
        return (not obj or obj.name != PublicDocumentType.CONTRIBUTION) and super().has_delete_permission(request, obj)


class FileScanEventInline(admin.TabularInline):
    model = FileScanEvent
    fields = ['created', 'operation', 'status', 'format', 'engine', 'detail', 'actor']
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class FileScanReviewForm(forms.Form):
    reason = forms.CharField(min_length=10, max_length=400, widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Explain the suspected false positive or corrected scanner problem. Do not include credentials or document content.')


class FalsePositiveForm(forms.Form):
    review_token = forms.CharField(widget=forms.HiddenInput)
    reason = forms.CharField(min_length=20, max_length=400, widget=forms.Textarea(attrs={'rows': 4}),
        help_text='Record how you investigated this particular finding and why you consider it a false positive. Knowing the sender alone is not a file safety check.')
    password = forms.CharField(strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
        label='Confirm your administrator password')
    accept_risk = forms.BooleanField(label='I understand that this exception can make the exact file publicly readable and downloadable, despite the antivirus finding.')

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        value = self.cleaned_data['password']
        if not self.user.check_password(value):
            raise ValidationError('The administrator password could not be confirmed.')
        return value


class FileScanExceptionInline(FileScanEventInline):
    model = FileScanException
    fields = ['format', 'detection', 'approved_at', 'approved_by', 'reason', 'revoked_at']
    readonly_fields = fields


class FileScanAdmin(admin.ModelAdmin):
    list_display = ['filename', 'status', 'manual_exception', 'size', 'created', 'checked', 'attempts']
    list_filter = ['status', 'created', 'checked']
    date_hierarchy = 'created'
    search_fields = ['sha256', 'filename']
    fields = ['sha256', 'filename', 'size', 'status', 'formats', 'rejected_formats', 'created', 'checked', 'attempts', 'engine', 'detail', 'retained_file', 'linked_contributions']
    readonly_fields = fields
    actions = ['retry_checks', 'approve_exception', 'revoke_exception', 'discard_private_uploads']
    inlines = [FileScanExceptionInline, FileScanEventInline]

    @admin.display(description='Original file in private quarantine')
    def retained_file(self, obj):
        from .upload_audit import scan_inventory
        report = scan_inventory(obj)
        return 'Retained privately; not published by this screen.' if report['private_file_retained'] else 'Not in private quarantine. Check the linked document archive or ask for resubmission.'

    @admin.display(description='Linked contributions')
    def linked_contributions(self, obj):
        from .upload_audit import related_contributions
        from django.urls import reverse
        from django.utils.html import format_html_join
        rows = [(reverse('admin:fgtida_contribution_change', args=[item.pk]), item.reference or str(item.pk))
                for item in related_contributions(obj).only('pk', 'number_prefix', 'sequence_number')]
        return format_html_join(', ', '<a href="{}">{}</a>', rows) if rows else 'No saved contribution association found. Run audit_fgtida_uploads with --match-originals for older records before requesting resubmission.'

    @admin.display(boolean=True, description='Active manual exception')
    def manual_exception(self, obj):
        return obj.status == 'rejected' and obj.exceptions.filter(detection=obj.detail, revoked_at__isnull=True).exclude(format__in=obj.rejected_formats).exists()

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description='Approve a reviewed antivirus false positive (one file)')
    @method_decorator(sensitive_post_parameters('password', 'review_token'))
    def approve_exception(self, request, queryset):
        return self._approve_exception(request, queryset)

    def _approve_exception(self, request, queryset):
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
        from django.core import signing
        if not request.user.is_active or not request.user.is_superuser:
            raise PermissionDenied
        if queryset.count() != 1:
            self.message_user(request, 'Select exactly one file for a reviewed exception.', level='error')
            return
        record = queryset.first()
        confirmed = request.POST.get('confirm_exception') == 'yes'
        form = FalsePositiveForm(request.user, request.POST if confirmed else None, initial={
            'review_token': signing.dumps([record.pk, record.detail], salt='fgtida-scan-exception')})
        if confirmed and form.is_valid():
            try:
                reviewed = signing.loads(form.cleaned_data['review_token'], salt='fgtida-scan-exception', max_age=600)
                if reviewed != [record.pk, record.detail]:
                    raise signing.BadSignature
            except signing.BadSignature:
                form.add_error(None, 'The review expired or the finding changed. Reopen the action and review the current finding.')
            else:
                return self._complete_exception(request, record, form)
        return self._exception_form_response(request, record, form, ACTION_CHECKBOX_NAME)

    def _complete_exception(self, request, record, form):
        from .file_safety import approve_false_positive
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
        try:
            granted = approve_false_positive(record.pk, request.user, form.cleaned_data['reason'], expected_detection=record.detail)
        except ValidationError as error:
            form.add_error(None, error)
            record.refresh_from_db()
        else:
            self.log_change(request, record, 'False-positive review: ' + form.cleaned_data['reason'])
            self.message_user(request, 'Exact-file antivirus exception approved. File and archive limits remain enforced.' if granted
                else 'The new scan passed. No manual exception was needed.')
            return
        return self._exception_form_response(request, record, form, ACTION_CHECKBOX_NAME)

    def _exception_form_response(self, request, record, form, checkbox_name):
        from django.template.response import TemplateResponse
        return TemplateResponse(request, 'fgtida/admin/approve_file_exception.html', {
            **self.admin_site.each_context(request), 'title': 'Review one antivirus false positive',
            'form': form, 'record': record, 'action_checkbox_name': checkbox_name, 'opts': self.model._meta,
        })

    @admin.action(description='Revoke manual antivirus exceptions for selected files')
    def revoke_exception(self, request, queryset):
        from .file_safety import revoke_exceptions
        if not request.user.is_active or not request.user.is_superuser:
            raise PermissionDenied
        count = 0
        for record in queryset:
            changed = revoke_exceptions(record, actor=request.user)
            count += changed
            if changed:
                self.log_change(request, record, 'Manual antivirus exception revoked.')
        self.message_user(request, f'Revoked {count} manual exception(s). Files with antivirus findings are blocked again.')

    @admin.action(description='Review and retry checks for selected files')
    def retry_checks(self, request, queryset):
        from django.template.response import TemplateResponse
        from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
        from .file_safety import quarantine_path, validate_file, record_event
        confirmed = request.POST.get('confirm_scan_retry') == 'yes'
        form = FileScanReviewForm(request.POST if confirmed else None)
        if not confirmed or not form.is_valid():
            return TemplateResponse(request, 'fgtida/admin/retry_file_scans.html', {
                **self.admin_site.each_context(request), 'title': 'Review and retry file security checks',
                'form': form, 'records': queryset.exclude(status__in=['clean', 'admin'])[:10],
                'action_checkbox_name': ACTION_CHECKBOX_NAME, 'opts': self.model._meta,
            })
        checked = 0
        for record in queryset.exclude(status__in=['clean', 'admin'])[:10]:
            record_event(record, 'review', actor=request.user, detail=form.cleaned_data['reason'])
            self.log_change(request, record, 'Security rescan requested: ' + form.cleaned_data['reason'])
            path = quarantine_path(record.sha256)
            if not path.is_file():
                record.status = 'pending'
                record.formats = []
                record.rejected_formats = []
                record.detail = 'Another scan requested. Submit the original file again, or run the existing-file scan command.'
                record.save(update_fields=['status', 'formats', 'rejected_formats', 'detail'])
                checked += 1
                continue
            data = path.read_bytes()
            import hashlib
            if hashlib.sha256(data).hexdigest() != record.sha256:
                continue
            try:
                validate_file(data, record.filename, force=True)
            except ValidationError:
                pass
            checked += 1
        self.message_user(request, f'Retried {checked} file(s). Check their updated status. Contributors can retry their held submissions after checks pass.')

    @admin.action(description='Remove selected held files from private quarantine')
    def discard_private_uploads(self, request, queryset):
        from .file_safety import quarantine_path, record_event
        removed = 0
        for record in queryset.exclude(status='clean'):
            quarantine_path(record.sha256).unlink(missing_ok=True)
            record_event(record, 'discard', actor=request.user, detail='Private upload removed by an administrator; scanner verdict retained.')
            self.log_change(request, record, 'Held bytes removed; scan decision retained.')
            removed += 1
        self.message_user(request, f'Removed {removed} held file(s). Scan audit records were preserved.')


class DocumentCategoryForm(forms.ModelForm):
    def clean_slug(self):
        slug = self.cleaned_data['slug']
        if not slug.startswith('tida-'):
            slug = 'tida-' + slug
        if slug == 'tida-uncatalogued':
            raise ValidationError('Choose a public category identifier such as tida-reports.')
        forms.SlugField(max_length=32).clean(slug)
        if self.instance.pk and self.instance.pk != slug:
            raise ValidationError('Keep the category identifier to preserve document assignments. You can change its display name.')
        return slug


class DocumentCategoryAdmin(admin.ModelAdmin):
    form = DocumentCategoryForm
    fields = ['slug', 'name', 'desc', 'used', 'order']
    list_display = ['name', 'slug', 'used']
    search_fields = ['name', 'slug']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(slug__startswith='tida-').exclude(slug='tida-uncatalogued')

    def get_readonly_fields(self, request, obj=None):
        return ['slug'] if obj else []

    def has_delete_permission(self, request, obj=None):
        return False


class FgDocumentAdmin(DocumentAdmin):
    form = DocumentForm
    fields = ['name', 'title', 'type', 'group', 'abstract', 'catalogued', 'lifecycle_state',
              'current_version', 'upload', 'version', 'document_direction', 'document_meeting', 'document_source', 'document_created', 'meeting_session', 'external_url', 'comment_about_changes']
    list_display = ['name', 'title', 'public_document_type', 'group', 'public_version', 'time']
    search_fields = ['name', 'title', 'abstract']
    list_filter = ['tida_attribution__public_type', 'tida_attribution__additional_types', 'type', 'group']
    raw_id_fields = []
    inlines = [DocAuthorInline, DocumentAttributionInline, RelatedDocumentInline, AdditionalUrlInLine, DocumentFormatInline]
    actions = []

    def get_queryset(self, request):
        from django.db.models import Exists, OuterRef
        return (super().get_queryset(request).filter(group__in=core.groups())
            .select_related('type', 'tida_attribution__public_type').prefetch_related('tida_versions')
            .annotate(is_contribution=Exists(Contribution.objects.filter(document_id=OuterRef('pk')))))

    @admin.display(description='Type')
    def public_document_type(self, obj):
        from .templatetags.fgtida import document_type
        return document_type(obj)

    def get_deleted_objects(self, objs, request):
        # Native history and author admins are hidden, but their records can
        # be removed as part of an authorized FG-TIDA document deletion.
        objs = list(objs)
        request._fgtida_deleting_document_ids = {obj.pk for obj in objs if self.has_delete_permission(request, obj)}
        try:
            return super().get_deleted_objects(objs, request)
        finally:
            del request._fgtida_deleting_document_ids

    @transaction.atomic
    def delete_queryset(self, request, queryset):
        prefixes = Contribution.objects.filter(document__in=queryset).exclude(number_prefix='').order_by('number_prefix')
        for prefix in prefixes.values_list('number_prefix', flat=True).distinct():
            ContributionSequence.lock(prefix)
        super().delete_queryset(request, queryset)

    @admin.display(description='Version')
    def public_version(self,obj):
        from .versioning import public_version
        return public_version(obj)

    def save_model(self, request, obj, form, change):
        from .github_catalog import preserve_manual_document
        upload = form.cleaned_data.get('upload')
        if change and 'current_version' in form.changed_data and form.cleaned_data.get('current_version'):
            from .versioning import correct_version
            correct_version(form.current_version_record, form.cleaned_data['current_version'], core.actor_for(request.user))
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
        from ietf.doc.models import DocumentAuthor
        edited_metadata = set()
        profiles_changed = False
        for formset in formsets:
            if formset.model == DocumentAttribution:
                for credit_form in formset.forms:
                    if not credit_form.cleaned_data.get('DELETE'):
                        edited_metadata.update(credit_form.changed_data)
            if formset.model == DocumentAuthor:
                profiles_changed = any(author_form.has_changed() for author_form in formset.forms)
        if profiles_changed:
            metadata, _ = DocumentAttribution.objects.get_or_create(document=form.instance)
            provenance = dict(metadata.credit_provenance or {})
            for field in ('authors', 'affiliations'):
                if field not in edited_metadata:
                    setattr(metadata, field, '')
                    provenance[field] = {'mode': 'profiles'}
            metadata.credit_provenance = provenance
            metadata.save()
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
    exclude = ['category', 'category_evidence']
    actions=['queue_sync']

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)
        class EvidenceForm(base):
            def clean(self):
                values = super().clean()
                from .classification import public_github_url
                group = values.get('group')
                if group and group.acronym != 'tida' and not public_github_url(values.get('group_evidence')):
                    self.add_error('group_evidence', 'Provide public GitHub evidence of this group assignment; otherwise select FG-TIDA without a wg assignment.')
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
                from .file_safety import validate_file
                upload.seek(0)
                validate_file(upload.read(MAX_BYTES + 1), upload.name)
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


from .meeting_admin import WindowInline, WindowAdmin, ParticipantAdmin
from .models import MeetingSubmissionWindow, MeetingParticipant


class FgMeetingAdmin(MeetingAdmin):
    inlines = [WindowInline, *MeetingAdmin.inlines]
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


def meeting_pk(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


class AssignmentInline(admin.TabularInline):
    model = SchedTimeSessAssignment
    fields = ['schedule', 'timeslot', 'pinned']
    verbose_name_plural = 'Schedule assignments'
    extra = 0

    def get_formset(self, request, obj=None, **kwargs):
        request._fgtida_meeting = obj.meeting_id if obj else request.POST.get('meeting') or request.GET.get('meeting')
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        meeting_id = meeting_pk(getattr(request, '_fgtida_meeting', None))
        if db_field.name == 'schedule':
            kwargs['queryset'] = Schedule.objects.filter(meeting__type_id='tida', meeting_id=meeting_id)
        elif db_field.name == 'timeslot':
            kwargs['queryset'] = TimeSlot.objects.filter(meeting__type_id='tida', meeting_id=meeting_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class FgSessionForm(forms.ModelForm):
    schedule_fields = {'agenda_start', 'agenda_minutes', 'agenda_room'}
    agenda_note = forms.CharField(required=False, label='Event topics', widget=forms.Textarea(attrs={'rows': 8}),
        help_text='Enter each topic on its own line, with its agenda number if available.')
    agenda_presenters = forms.CharField(required=False, label='Lead / Presenter', widget=forms.Textarea(attrs={'rows': 5}),
        help_text='Enter one presenter or team per line. Include the topic number when a presenter covers a specific item.')
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
        self.fields['agenda_room'].queryset = Room.objects.filter(meeting_id=meeting_pk(meeting_id), meeting__type_id='tida')
        if self.instance.pk:
            from zoneinfo import ZoneInfo
            assignments = list(self.instance.timeslotassignments.filter(
                schedule_id=self.instance.meeting.schedule_id,
            ).select_related('timeslot')[:2])
            if len(assignments) == 1:
                slot = assignments[0].timeslot
                self.fields['agenda_start'].initial = slot.time.astimezone(ZoneInfo(self.instance.meeting.time_zone or 'UTC')).strftime('%Y-%m-%dT%H:%M')
                self.fields['agenda_minutes'].initial = int(slot.duration.total_seconds() / 60)
                self.fields['agenda_room'].initial = slot.location_id
            elif assignments:
                self.fields['agenda_start'].help_text = 'This session has multiple times. Edit them in Schedule assignments below.'
        if self.instance.pk:
            self.fields['scheduling_status'].initial = Session.objects.with_current_status().get(pk=self.instance.pk).current_status

    def clean(self):
        from datetime import datetime, timedelta, timezone as dt_timezone
        from zoneinfo import ZoneInfo
        values = super().clean()
        meeting = values.get('meeting')
        if self.instance.pk and meeting and meeting.pk != self.instance.meeting_id:
            self.add_error('meeting', 'Create a new session to move to another meeting; existing materials and schedules belong to this meeting.')
        if values.get('agenda_start') and meeting and (
            not self.instance.pk or self.schedule_fields.intersection(self.changed_data)
        ):
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
              'agenda_start', 'agenda_minutes', 'agenda_room', 'agenda_note', 'agenda_presenters', 'agenda_outcome', 'agenda_open_ended',
              'remote_instructions', 'on_agenda', 'manage_materials']
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


def can_delete_document_child(request, obj=None):
    document_ids = getattr(request, '_fgtida_deleting_document_ids', ())
    if obj is None or not document_ids:
        return False
    from ietf.doc.models import DocEvent, DocHistory
    for field in obj._meta.fields:
        if not field.many_to_one:
            continue
        if field.related_model is Document and getattr(obj, field.attname) in document_ids:
            return True
        if field.related_model in (DocHistory, DocEvent):
            parent = getattr(obj, field.name)
            if parent and parent.doc_id in document_ids:
                return True
    return False


def configure_admin():
    if not admin.site.is_registered(TrainingCategory):
        admin.site.register(TrainingCategory)
    if not admin.site.is_registered(TrainingVideo):
        admin.site.register(TrainingVideo, TrainingVideoAdmin)
    if not admin.site.is_registered(SecurityQuestion):
        admin.site.register(SecurityQuestion, SecurityQuestionAdmin)
    for model, cls in [(User, FgUserAdmin), (Person, FgPersonAdmin), (Contribution, ContributionAdmin), (ContributionRecipient, ContributionRecipientAdmin), (SessionResource, SessionResourceAdmin), (OutgoingMail, OutgoingMailAdmin),
        (MeetingSubmissionWindow, WindowAdmin), (MeetingParticipant, ParticipantAdmin), (PublicDocumentType, PublicDocumentTypeAdmin), (FileScan, FileScanAdmin),
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
            elif model is SchedTimeSessAssignment:
                # The internal extension pointer is nullable and has no use
                # when an editor creates a schedule assignment.
                cls.exclude = ('extendedfrom',)
                cls.raw_id_fields = [field for field in cls.raw_id_fields if field != 'extendedfrom']
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
                if opts.label_lower == 'otp_totp.totpdevice' else
                can_delete_document_child if opts.app_label == 'doc' else hidden_admin_permission)
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
            for model in app['models']:
                if app['app_label'] == 'name' and model['object_name'] == 'DocTagName':
                    model['name'] = 'Document categories'
        return [app for app in apps if app['models']]

    admin.site.get_app_list = MethodType(app_list, admin.site)
