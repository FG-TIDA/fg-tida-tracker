from django.conf import settings
from django.core.validators import URLValidator
from django.db import models
from django.utils import timezone


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
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='administrator_approvals')
    created = models.DateTimeField(auto_now_add=True)


class OutgoingMail(models.Model):
    purpose = models.CharField(max_length=16, default='general')
    contribution = models.ForeignKey('Contribution', null=True, blank=True, on_delete=models.PROTECT)
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    html_body = models.TextField(blank=True, db_default='')
    created = models.DateTimeField(auto_now_add=True)
    sent = models.DateTimeField(null=True, blank=True, db_index=True)
    expires = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=200, blank=True)


class Contribution(models.Model):
    """Private review metadata linked to a public submitted document."""
    submitter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    notifications_queued = models.BooleanField(default=True, editable=False)
    group = models.ForeignKey('group.Group', on_delete=models.PROTECT)
    title = models.CharField(max_length=255)
    abstract = models.TextField(blank=True)
    draft_repository_url = models.URLField(blank=True, max_length=300)
    work_item_acronym = models.SlugField(max_length=48, blank=True)
    filename = models.CharField(max_length=255)
    content = models.BinaryField(editable=False)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    status = models.CharField(max_length=16, default='pending', db_index=True,
        choices=[('pending', 'Awaiting review'), ('accepted', 'Accepted'), ('rejected', 'Rejected')])
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='reviewed_contributions')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    document = models.ForeignKey('doc.Document', null=True, blank=True, on_delete=models.SET_NULL)

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


class EmailVerification(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    email = models.EmailField()
    created = models.DateTimeField(auto_now_add=True)
    verified = models.DateTimeField(null=True, blank=True)


class GitHubAccount(models.Model):
    organization = models.SlugField(max_length=100, unique=True)
    installation_id = models.PositiveBigIntegerField(null=True, blank=True,
        help_text='GitHub App installation ID; no tokens or private keys belong in this form.')
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return self.organization


class WorkingGroupConfiguration(models.Model):
    group = models.OneToOneField('group.Group', on_delete=models.CASCADE, related_name='tida_configuration')
    number = models.PositiveIntegerField(unique=True)
    topic_acronym = models.SlugField(max_length=32)
    github_account = models.ForeignKey(GitHubAccount, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f'wg{self.number}: {self.group.name}'


class GitHubRepository(models.Model):
    account = models.ForeignKey(GitHubAccount, on_delete=models.PROTECT)
    name = models.SlugField(max_length=100)
    github_id = models.PositiveBigIntegerField(null=True, blank=True, unique=True)
    group = models.ForeignKey('group.Group', on_delete=models.PROTECT)
    branch = models.SlugField(default='main', max_length=100)
    publish_workflow = models.CharField(default='.github/workflows/docs.yml', max_length=200)
    mode = models.CharField(max_length=16, default='workitem', choices=[
        ('workitem', 'Versioned work item'), ('files', 'Document files'), ('issues', 'Theme / use-case issues')])
    category = models.CharField(max_length=32, default='', blank=True)
    group_evidence = models.URLField(max_length=2000, blank=True, help_text='Public GitHub page explicitly establishing the working-group assignment. A topic or a WG mention is not ownership.')
    category_evidence = models.URLField(max_length=2000, blank=True, help_text='Public GitHub page explicitly describing this source collection. Leave category blank when not established.')
    document_type = models.CharField(max_length=24, default='tida')
    paths = models.JSONField(default=list, blank=True, help_text='Explicit document paths; support/code files are never automatically documents.')
    enabled = models.BooleanField(default=True)
    last_sync = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'name'], name='fgtida_github_repository_unique')]

    @property
    def full_name(self):
        return f'{self.account.organization}/{self.name}'

    @property
    def url(self):
        return 'https://github.com/' + self.full_name

    def __str__(self):
        return self.full_name


class GitHubSource(models.Model):
    repository = models.ForeignKey(GitHubRepository, on_delete=models.PROTECT)
    source_key = models.CharField(max_length=500)
    document = models.ForeignKey('doc.Document', null=True, blank=True, on_delete=models.SET_NULL, related_name='github_sources')
    sync_mode = models.CharField(max_length=16, default='automatic', choices=[
        ('automatic', 'Sync from GitHub'), ('manual', 'Keep manual edits'), ('excluded', 'Excluded / deleted by administrator')])
    sha = models.CharField(max_length=64, blank=True)
    checksum = models.CharField(max_length=64, blank=True)
    source_url = models.URLField(max_length=2000, blank=True)
    last_sync = models.DateTimeField(null=True, blank=True)
    source_metadata = models.JSONField(default=dict, blank=True, help_text='Observed GitHub labels/state and classification evidence; no inferred WG ownership.')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['repository', 'source_key'], name='fgtida_github_source_unique')]

    def __str__(self):
        return f'{self.repository}: {self.source_key}'


class WorkItem(models.Model):
    document = models.OneToOneField('doc.Document', null=True, blank=True, on_delete=models.SET_NULL, related_name='tida_work_item')
    repository = models.OneToOneField(GitHubRepository, null=True, blank=True, on_delete=models.SET_NULL, related_name='work_item')
    short_name = models.SlugField(max_length=48)
    sync_mode = models.CharField(max_length=16, default='automatic', choices=GitHubSource._meta.get_field('sync_mode').choices)
    working_sha = models.CharField(max_length=40, blank=True)
    working_pdf = models.BinaryField(blank=True, default=bytes, editable=False)
    working_manifest = models.JSONField(default=dict, blank=True)
    working_checksum = models.CharField(max_length=64, blank=True)
    working_updated = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.document.title if self.document else f'Retired work item {self.pk}'


class WorkItemRelease(models.Model):
    work_item = models.ForeignKey(WorkItem, on_delete=models.PROTECT, related_name='releases')
    tag = models.CharField(max_length=48)
    version = models.JSONField()
    revision = models.CharField(max_length=16)
    commit_sha = models.CharField(max_length=40)
    checksum = models.CharField(max_length=64)
    manifest = models.JSONField()
    source_archive = models.BinaryField(editable=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['work_item', 'tag'], name='fgtida_release_tag_unique'),
            models.UniqueConstraint(fields=['work_item', 'revision'], name='fgtida_release_revision_unique')]
        ordering = ['-created']


class GitHubJob(models.Model):
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    kind = models.CharField(max_length=16, choices=[('provision', 'Create WG repository'), ('state', 'Publish lifecycle state'), ('catalog', 'Synchronize catalog')])
    work_item = models.ForeignKey(WorkItem, null=True, blank=True, on_delete=models.PROTECT)
    repository = models.ForeignKey(GitHubRepository, null=True, blank=True, on_delete=models.PROTECT)
    key = models.CharField(max_length=200, unique=True)
    created = models.DateTimeField(auto_now_add=True)
    completed = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=200, blank=True)


class GitHubDelivery(models.Model):
    delivery_id = models.UUIDField(primary_key=True)
    received = models.DateTimeField(auto_now_add=True)
    repository = models.ForeignKey(GitHubRepository, null=True, on_delete=models.SET_NULL)
    event = models.CharField(max_length=32)


class DocumentVersion(models.Model):
    """Public X.Y version, independent of immutable native archive identifiers."""
    document = models.ForeignKey('doc.Document', on_delete=models.CASCADE, related_name='tida_versions')
    revision = models.CharField(max_length=16)
    version = models.CharField(max_length=32)
    recorded = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['document','revision'],name='fgtida_version_revision_unique'),
            models.UniqueConstraint(fields=['document','version'],name='fgtida_version_label_unique')]


class DocumentListing(models.Model):
    """Editorial classification of an input/output, using the native archive."""
    document = models.OneToOneField('doc.Document', on_delete=models.CASCADE, related_name='tida_listing')
    direction = models.CharField(max_length=6, choices=[('input', 'Input document'), ('output', 'Output document')], db_index=True)
    meeting = models.ForeignKey('meeting.Meeting', blank=True, null=True, on_delete=models.SET_NULL)
    source = models.CharField(max_length=255, blank=True, help_text='Submitting organization, author or working group.')
    created = models.DateTimeField(default=timezone.now, db_index=True)


class DocumentFormatManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().defer('content')


class DocumentFormat(models.Model):
    """Optional editorial Word/PDF alternatives belonging to one archive revision."""
    document = models.ForeignKey('doc.Document', on_delete=models.CASCADE, related_name='tida_formats')
    revision = models.CharField(max_length=16)
    kind = models.CharField(max_length=4, choices=[('pdf', 'PDF'), ('docx', 'Word')])
    filename = models.CharField(max_length=255, editable=False)
    content = models.BinaryField(editable=False)
    updated = models.DateTimeField(auto_now=True)
    objects = DocumentFormatManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['document', 'revision', 'kind'], name='fgtida_document_format_unique')]
