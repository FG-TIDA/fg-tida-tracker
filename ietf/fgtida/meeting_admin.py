from datetime import datetime, timezone as utc_timezone
from django import forms
from django.contrib import admin
from django.utils import timezone
from . import core
from .models import MeetingSubmissionWindow, MeetingParticipant


class UTCField(forms.Field):
    widget = forms.TextInput(attrs={'placeholder': '2026-11-23 23:59 UTC'})

    def to_python(self, value):
        if not value:
            return None
        if isinstance(value, datetime):
            stamp = value
        else:
            try:
                stamp = datetime.fromisoformat(value.strip().replace(' UTC', '+00:00').replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                raise forms.ValidationError('Enter YYYY-MM-DD HH:MM UTC, for example 2026-11-23 23:59 UTC.')
        return stamp.replace(tzinfo=utc_timezone.utc) if timezone.is_naive(stamp) else stamp.astimezone(utc_timezone.utc)

    def prepare_value(self, value):
        return value.astimezone(utc_timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if isinstance(value, datetime) else value


class WindowForm(forms.ModelForm):
    deadline = UTCField(required=False, label='Submission deadline (UTC)',
        help_text='Use YYYY-MM-DD HH:MM UTC. Blank closes at the start of the meeting in its local timezone.')
    reopen_until = UTCField(required=False, label='Reopen / extend until (UTC)',
        help_text='Enable Accepting submissions and set the final UTC closing time. Overrides the original deadline while set; clear to restore it.')

    class Meta:
        model = MeetingSubmissionWindow
        fields = ['meeting', 'accepting_submissions', 'deadline', 'reopen_until']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'meeting' in self.fields:
            self.fields['meeting'].queryset = core.meetings()


class WindowInline(admin.StackedInline):
    model = MeetingSubmissionWindow
    form = WindowForm
    extra = 1
    max_num = 1
    verbose_name_plural = 'NWI submission deadline — all dates and times below are UTC'


class WindowAdmin(admin.ModelAdmin):
    form = WindowForm
    list_display = ['meeting', 'accepting_submissions', 'deadline', 'reopen_until']
    list_filter = ['accepting_submissions']

    def get_readonly_fields(self, request, obj=None):
        return ['meeting'] if obj else []

    def get_queryset(self, request):
        return super().get_queryset(request).filter(meeting__type_id='tida').select_related('meeting')


class ParticipantAdmin(admin.ModelAdmin):
    list_display = ['display_name', 'affiliation', 'country', 'meeting', 'attendance', 'published']
    list_filter = ['meeting', 'attendance', 'published']
    search_fields = ['full_name', 'first_name', 'last_name', 'affiliation', 'country']

    @admin.display(description='Full Name', ordering='full_name')
    def display_name(self, obj):
        return obj.display_name

    def get_queryset(self, request):
        return super().get_queryset(request).filter(meeting__type_id='tida').select_related('meeting')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'meeting':
            kwargs['queryset'] = core.meetings()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
