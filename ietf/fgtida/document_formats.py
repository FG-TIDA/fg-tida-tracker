"""Validated, revision-specific editorial alternatives stored in PostgreSQL."""
import io
from pathlib import Path
from django import forms
from django.forms.models import BaseInlineFormSet
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_safe
from . import core
from .files import extract_text, MAX_BYTES
from .models import DocumentFormat


class DocumentFormatForm(forms.ModelForm):
    upload = forms.FileField(required=False, label='Word / PDF / RST ZIP file', help_text='Optional alternative for this revision, up to 10 MB. Uploading it does not replace the source document. Public when the document is catalogued.')

    class Meta:
        model = DocumentFormat
        fields = ['kind', 'upload']

    def clean(self):
        values = super().clean()
        upload = values.get('upload')
        if upload:
            extension = Path(upload.name).suffix.lower().lstrip('.')
            if extension not in {'pdf', 'docx', 'zip'} or extension != values.get('kind'):
                self.add_error('upload', 'Select PDF for .pdf, Word for .docx, or RST ZIP package for .zip.')
            else:
                try:
                    self._content = upload.read(MAX_BYTES + 1)
                    extract_text(self._content, upload.name)
                except forms.ValidationError as error:
                    self.add_error('upload', error)
                finally:
                    upload.seek(0)
        elif not self.instance.pk:
            self.add_error('upload', 'Attach a PDF, Word or RST ZIP file.')
        elif values.get('kind') != self.instance.kind:
            self.add_error('upload', 'Upload a matching replacement file when changing its format.')
        return values

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data.get('upload'):
            import hashlib
            instance.filename = Path(self.cleaned_data['upload'].name).name[:255]
            instance.content = self._content
            instance.sha256 = hashlib.sha256(self._content).hexdigest()
        if commit:
            instance.save()
        return instance


class DocumentFormatFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        uploading = getattr(self.instance, '_fgtida_upload_revision', False)
        target = f'{int(self.instance.rev or "-1") + 1:02d}' if uploading else self.instance.rev
        seen = set()
        for form in self.forms:
            values = getattr(form, 'cleaned_data', {})
            if not values or values.get('DELETE'):
                continue
            if not self.instance.uploaded_filename and not uploading:
                raise forms.ValidationError('Upload a source revision in the main Upload field before adding alternative formats.')
            key = (form.instance.revision if form.instance.pk else target, values.get('kind'))
            if key in seen:
                raise forms.ValidationError('Keep at most one alternative of each format per revision.')
            seen.add(key)

    def save_new(self, form, commit=True):
        form.instance.revision = self.instance.rev
        return super().save_new(form, commit=commit)


@require_safe
def download(request, pk):
    record = get_object_or_404(DocumentFormat.objects.select_related('document').defer('content'), pk=pk, document__in=core.documents())
    if not record.is_public:
        user = request.user
        owner = user.is_authenticated and record.document.contribution_set.filter(submitter=user).exists()
        editor = user.is_authenticated and user.has_perm('fgtida.change_contribution') and user.has_perm('doc.change_document')
        if not (owner or editor):
            raise Http404
    from .versioning import public_version
    filename = f'{record.document.name}-{public_version(record.document, record.revision)}.{record.kind}'
    from .file_safety import require_clean
    require_clean(bytes(record.content), record.filename)
    mime = {'pdf': 'application/pdf', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'zip': 'application/zip'}[record.kind]
    return FileResponse(io.BytesIO(bytes(record.content)), as_attachment=True, filename=filename, content_type=mime,
        headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})
