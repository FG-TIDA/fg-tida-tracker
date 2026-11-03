from django.conf import settings
from django.core.validators import URLValidator
from django.db import models


class RecoveryQuestions(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    questions = models.JSONField(default=list)
    prompts = models.JSONField(default=dict)
    answer_hashes = models.JSONField(default=list)
    failures = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)


class SecurityQuestion(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    prompt = models.CharField(max_length=255)
    active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'pk']

    def __str__(self):
        return self.prompt


class MfaRecoveryCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    digest = models.CharField(max_length=64)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'digest'], name='fgtida_mfa_code_unique')]


class AuthenticationAttempt(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    failures = models.PositiveIntegerField(default=0)
    expires = models.DateTimeField(db_index=True)


class AdministratorApproval(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='administrator_approvals')
    created = models.DateTimeField(auto_now_add=True)


class OutgoingMail(models.Model):
    contribution = models.ForeignKey('Contribution', null=True, blank=True, on_delete=models.PROTECT)
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    created = models.DateTimeField(auto_now_add=True)
    sent = models.DateTimeField(null=True, blank=True, db_index=True)
    expires = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=200, blank=True)


class Contribution(models.Model):
    """Private review queue; only accepted submissions become native Documents."""
    submitter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    notifications_queued = models.BooleanField(default=True, editable=False)
    group = models.ForeignKey('group.Group', on_delete=models.PROTECT)
    title = models.CharField(max_length=255)
    abstract = models.TextField(blank=True)
    filename = models.CharField(max_length=255)
    content = models.BinaryField(editable=False)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    status = models.CharField(max_length=16, default='pending', db_index=True,
        choices=[('pending', 'Awaiting review'), ('accepted', 'Accepted'), ('rejected', 'Rejected')])
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='reviewed_contributions')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    document = models.ForeignKey('doc.Document', null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ['-created']
        indexes = [models.Index(fields=['submitter', 'status', '-created'], name='fgtida_contrib_queue_idx')]

    def __str__(self):
        return self.title


class ContributionRecipient(models.Model):
    group = models.ForeignKey('group.Group', on_delete=models.CASCADE, related_name='contribution_recipients')
    email = models.EmailField(help_text='WG member address, or management address for Incubation.')
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'email'], name='fgtida_group_recipient_unique')]

    def __str__(self):
        return f'{self.group}: {self.email}'


class SessionResource(models.Model):
    session = models.ForeignKey('meeting.Session', on_delete=models.CASCADE, related_name='tida_resources')
    kind = models.CharField(max_length=16, choices=[('recording', 'Recording'), ('notes', 'Notes'),
        ('wiki', 'Wiki'), ('materials', 'Materials / PDF'), ('login', 'Remote participation / login')])
    title = models.CharField(max_length=160)
    url = models.URLField(max_length=2000, blank=True, validators=[URLValidator(schemes=['https', 'http'])])
    filename = models.CharField(max_length=255, blank=True, editable=False)
    file_size = models.PositiveBigIntegerField(default=0, editable=False)
    content_type = models.CharField(max_length=100, blank=True, editable=False)
    file_version = models.CharField(max_length=32, blank=True, editable=False)

    class Meta:
        ordering = ['kind', 'pk']

    @property
    def icon(self):
        return {'recording': 'camera-video', 'notes': 'chat-left-text', 'wiki': 'book',
                'materials': 'file-earmark-pdf', 'login': 'headset'}[self.kind]

    def __str__(self):
        return self.title

    @property
    def resource_url(self):
        from django.urls import reverse
        return reverse('fgtida:session-resource-file', args=[self.pk]) if self.file_size else self.url

    def save(self, *args, **kwargs):
        from django.db import transaction
        upload = getattr(self, '_pending_upload', None)
        with transaction.atomic():
            super().save(*args, **kwargs)
            if upload:
                from .resource_files import store_upload
                store_upload(self, upload)
                del self._pending_upload


class SessionResourceChunk(models.Model):
    resource = models.ForeignKey(SessionResource, on_delete=models.CASCADE, related_name='file_chunks')
    position = models.PositiveIntegerField()
    content = models.BinaryField()

    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['resource', 'position'], name='fgtida_resource_chunk_position')]
