from pathlib import Path

from django import forms
from django.contrib import admin
from django.db import transaction
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_safe

from .models import TrainingVideo
from .resource_files import MEDIA_LIMIT, store_upload, stream_file


class TrainingVideoForm(forms.ModelForm):
    upload = forms.FileField(required=False, label='Video file',
        help_text='Upload an MP4 (H.264 recommended) or WebM file up to 100 MB.')

    class Meta:
        model = TrainingVideo
        fields = ['title', 'description', 'upload', 'published']

    def clean_upload(self):
        upload = self.cleaned_data['upload']
        if not upload:
            if not self.instance.file_size:
                raise forms.ValidationError('Choose a video file.')
            return upload
        suffix = Path(upload.name).suffix.lower()
        header = upload.read(16)
        upload.seek(0)
        valid = {'.mp4': header[4:8] == b'ftyp', '.webm': header.startswith(b'\x1aE\xdf\xa3')}
        if not valid.get(suffix) or upload.size > MEDIA_LIMIT:
            raise forms.ValidationError('Upload a valid MP4 or WebM file up to 100 MB.')
        return upload


class TrainingVideoAdmin(admin.ModelAdmin):
    form = TrainingVideoForm
    list_display = ['title', 'published', 'filename']
    list_filter = ['published']
    search_fields = ['title', 'description']
    readonly_fields = ['filename']
    fields = ['title', 'description', 'upload', 'filename', 'published']

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        # An editor changing only the description must not overwrite the file
        # version if another editor replaced the video in the meantime.
        if change:
            TrainingVideo.objects.select_for_update().get(pk=obj.pk)
            obj.save(update_fields=['title', 'description', 'published'])
        else:
            super().save_model(request, obj, form, change)
        if form.cleaned_data['upload']:
            store_upload(obj, form.cleaned_data['upload'])


@require_safe
def training(request):
    videos = TrainingVideo.objects.filter(published=True, file_size__gt=0)
    return render(request, 'fgtida/training.html', {'videos': videos})


@require_safe
def video(request, pk):
    record = get_object_or_404(TrainingVideo, pk=pk, published=True, file_size__gt=0)
    return stream_file(request, record)
