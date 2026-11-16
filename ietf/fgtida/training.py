from pathlib import Path

from django import forms
from django.contrib import admin
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_safe

from .files import MAX_BYTES, extract_text
from .models import TrainingVideo
from .resource_files import MEDIA_LIMIT, store_upload, stream_file


class TrainingResourceForm(forms.ModelForm):
    upload = forms.FileField(required=False, label='Training file',
        help_text='Upload an MP4 or WebM up to 100 MB, or a PDF up to 10 MB. Choosing an external link replaces any attached file.')

    class Meta:
        model = TrainingVideo
        fields = ['title', 'description', 'category', 'kind', 'external_url', 'upload', 'published']

    def clean(self):
        values = super().clean()
        kind, upload, url = (values.get(field) for field in ('kind', 'upload', 'external_url'))
        if kind == TrainingVideo.Kind.LINK:
            if not url and 'external_url' not in self.errors:
                self.add_error('external_url', 'Enter the HTTPS URL for this resource.')
            if upload:
                self.add_error('upload', 'Clear the upload when publishing an external link.')
            return values
        if url:
            self.add_error('external_url', 'Clear the URL when publishing an uploaded file.')
        if not upload:
            if not self.instance.file_size or kind != self.instance.kind:
                self.add_error('upload', 'Choose a file for this resource.')
            return values
        suffix = Path(upload.name).suffix.lower()
        try:
            if kind == TrainingVideo.Kind.GUIDE:
                if suffix != '.pdf' or upload.size > MAX_BYTES:
                    raise forms.ValidationError('Upload a PDF file up to 10 MB.')
                extract_text(upload.read(MAX_BYTES + 1), upload.name)
            elif kind == TrainingVideo.Kind.VIDEO:
                header = upload.read(16)
                valid = {'.mp4': header[4:8] == b'ftyp', '.webm': header.startswith(b'\x1aE\xdf\xa3')}
                if not valid.get(suffix) or upload.size > MEDIA_LIMIT:
                    raise forms.ValidationError('Upload a valid MP4 or WebM file up to 100 MB.')
        except forms.ValidationError as error:
            self.add_error('upload', error)
        finally:
            upload.seek(0)
        return values


class TrainingVideoAdmin(admin.ModelAdmin):
    form = TrainingResourceForm
    list_display = ['title', 'kind', 'category', 'published']
    list_filter = ['kind', 'category', 'published']
    search_fields = ['title', 'description', 'category__name']
    readonly_fields = ['filename']
    fields = ['title', 'description', 'category', 'kind', 'external_url', 'upload', 'filename', 'published']

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        upload = form.cleaned_data['upload']
        if change:
            TrainingVideo.objects.select_for_update().get(pk=obj.pk)
            fields = set(form.changed_data).intersection({'title', 'description', 'category', 'kind', 'external_url', 'published'})
            if upload or fields.intersection({'kind', 'external_url'}):
                fields.update({'kind', 'external_url'})
                if obj.kind == TrainingVideo.Kind.LINK:
                    obj.file_chunks.all().delete()
                    obj.filename = obj.file_version = obj.content_type = ''
                    obj.file_size = 0
                    fields.update({'filename', 'file_version', 'content_type', 'file_size'})
            obj.save(update_fields=fields)
        else:
            super().save_model(request, obj, form, change)
        if upload:
            store_upload(obj, upload)


def published_resources():
    return TrainingVideo.objects.filter(published=True).filter(
        Q(kind=TrainingVideo.Kind.LINK, external_url__istartswith='https://') |
        Q(kind__in=(TrainingVideo.Kind.VIDEO, TrainingVideo.Kind.GUIDE), file_size__gt=0)
    )


@require_safe
def training(request):
    resources = published_resources().select_related('category').order_by('category__name', 'title', 'pk')
    return render(request, 'fgtida/training.html', {'resources': resources})


@require_safe
def player(request, pk):
    resource = get_object_or_404(published_resources(), pk=pk)
    return render(request, 'fgtida/training_player.html', {'resource': resource})


@require_safe
def video(request, pk, download=False):
    resource = get_object_or_404(published_resources().exclude(kind=TrainingVideo.Kind.LINK), pk=pk)
    return stream_file(request, resource, attachment=download)
