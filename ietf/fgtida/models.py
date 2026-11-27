import re

from django.conf import settings
from django.core.validators import MinValueValidator, URLValidator
from django.db import models, router, transaction
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
    contribution = models.ForeignKey('Contribution', null=True, blank=True, on_delete=models.SET_NULL)
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    html_body = models.TextField(blank=True, default='', db_default='')
    created = models.DateTimeField(auto_now_add=True)
    sent = models.DateTimeField(null=True, blank=True, db_index=True)
    expires = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=200, blank=True)


class ContributionSequence(models.Model):
    prefix = models.CharField(max_length=50, primary_key=True)
    last_number = models.PositiveBigIntegerField(default=0)

    @classmethod
    def lock(cls, prefix, using=None):
        # Older fixtures may carry contribution records without their counters.
        def existing_maximum():
            return Contribution.objects.using(using).filter(
                number_prefix=prefix, sequence_number__isnull=False,
            ).order_by('-sequence_number').values_list('sequence_number', flat=True).first() or 0

        sequence, _ = cls.objects.using(using).select_for_update().get_or_create(
            prefix=prefix, defaults={'last_number': existing_maximum},
        )
        return sequence

    def reorder(self, moved=None, position=None):
        """Compact the visible order while leaving contribution and document IDs intact."""
        using = self._state.db
        rows = list(Contribution.objects.using(using).filter(number_prefix=self.prefix).order_by('sequence_number', 'created', 'pk'))
        if moved is not None:
            rows.remove(moved)
            rows.insert(position - 1, moved)
        changed = []
        for number, row in enumerate(rows, start=1):
            if row.sequence_number != number or row == moved:
                row.sequence_number = number
                changed.append(row)
        if changed:
            records = Contribution.objects.using(using)
            records.filter(pk__in=[row.pk for row in changed]).update(sequence_number=None)
            records.bulk_update(changed, ['sequence_number'])
        self.last_number = len(rows)
        self.save(using=using, update_fields=['last_number'])


class Contribution(models.Model):
    """Private review metadata linked to a public submitted document."""
    submitter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    contact_name = models.CharField(max_length=255, blank=True, help_text='Contributor name; no platform account is required.')
    contact_email = models.EmailField(blank=True, help_text='Contact email; included in document metadata and used for the receipt.')
    contact_affiliation = models.CharField(max_length=255, blank=True)
    notifications_queued = models.BooleanField(default=True, editable=False)
    group = models.ForeignKey('group.Group', on_delete=models.PROTECT)
    meeting = models.ForeignKey('meeting.Meeting', null=True, blank=True, on_delete=models.PROTECT,
        related_name='tida_contributions', help_text='Meeting this contribution is submitted for.')
    number_prefix = models.CharField(max_length=50, blank=True, editable=False, db_default='')
    sequence_number = models.PositiveBigIntegerField(null=True, validators=[MinValueValidator(1)],
        help_text='Position in the submission list. Moving this item shifts the other display numbers.')
    title = models.CharField(max_length=255)
    abstract = models.TextField(blank=True)
    draft_repository_url = models.URLField(blank=True, max_length=300)
    submission_method = models.CharField(max_length=12, default='legacy', choices=[
        ('legacy', 'Previous file submission'), ('github', 'GitHub repository'),
        ('rst', 'RST source ZIP'), ('word', 'Word (conversion assistance)')])
    source_commit = models.CharField(max_length=40, blank=True)
    source_folder = models.CharField(max_length=255, blank=True)
    entry_document = models.CharField(max_length=255, blank=True)
    source_archive = models.BinaryField(default=bytes, editable=False)
    source_manifest = models.JSONField(default=dict, editable=False)
    conversion_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    conversion_verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='verified_conversions', editable=False)
    work_item_acronym = models.SlugField(max_length=48, blank=True)
    filename = models.CharField(max_length=255)
    content = models.BinaryField(editable=False)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    status = models.CharField(max_length=16, default='pending', db_index=True,
        choices=[('pending', 'Awaiting review'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('catalogued', 'Imported from GitHub')])
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='reviewed_contributions')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    document = models.ForeignKey('doc.Document', null=True, blank=True, on_delete=models.CASCADE)

    class Meta:
        ordering = ['-created']
        indexes = [models.Index(fields=['submitter', 'status', '-created'], name='fgtida_contrib_queue_idx')]
        constraints = [
            models.UniqueConstraint(fields=['number_prefix', 'sequence_number'], name='fgtida_contribution_number_unique'),
            models.CheckConstraint(condition=models.Q(sequence_number__gt=0), name='fgtida_contribution_number_positive'),
        ]

    def submission_prefix(self):
        group = self.group
        config = getattr(group, 'tida_configuration', None)
        if config is not None:
            return f'FG-TIDA-WG{config.number}'
        if group.acronym == 'tida':
            return 'FG-TIDA'
        return f'FG-TIDA-{group.acronym.upper().removeprefix("TIDA")}'

    @property
    def reference(self):
        if self.sequence_number is None:
            return ''
        return f'{self.number_prefix}-{self.sequence_number:03d}'

    @staticmethod
    def reference_parts(value):
        match = re.fullmatch(r'(FG-TIDA(?:-[A-Z0-9_-]+)?)-([0-9]{1,19})', value.strip().upper())
        if match and 0 < int(match[2]) <= 2**63 - 1:
            return match[1], int(match[2])

    def save(self, *args, **kwargs):
        if not self._state.adding:
            return super().save(*args, **kwargs)
        using = kwargs.get('using') or router.db_for_write(type(self), instance=self)
        self.number_prefix = self.submission_prefix()
        with transaction.atomic(using=using):
            sequence = ContributionSequence.lock(self.number_prefix, using)
            if self.sequence_number is None:
                self.sequence_number = sequence.last_number + 1
            sequence.last_number = max(sequence.last_number, self.sequence_number)
            sequence.save(update_fields=['last_number'])
            return super().save(*args, **kwargs)

    def __str__(self):
        return self.title


def compact_contribution_numbers(sender, instance, using, **kwargs):
    if instance.number_prefix:
        ContributionSequence.lock(instance.number_prefix, using).reorder()


def lock_contribution_numbers(sender, instance, using, **kwargs):
    if instance.number_prefix:
        ContributionSequence.lock(instance.number_prefix, using)
    OutgoingMail.objects.using(using).filter(contribution=instance, sent__isnull=True).update(
        expires=timezone.now(), last_error='Work item removed; notification cancelled.')


class ContributionRecipient(models.Model):
    group = models.ForeignKey('group.Group', on_delete=models.CASCADE, related_name='contribution_recipients')
    email = models.EmailField(help_text='WG member address, or management address for Incubation.')
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'email'], name='fgtida_group_recipient_unique')]

    def __str__(self):
        return f'{self.group}: {self.email}'


class SessionResource(models.Model):
    sha256 = models.CharField(max_length=64, blank=True, editable=False)
    session = models.ForeignKey('meeting.Session', on_delete=models.CASCADE, related_name='tida_resources')
    kind = models.CharField(max_length=16, choices=[('recording', 'Recording'), ('notes', 'Notes'),
        ('agenda', 'Agenda'), ('slides', 'Slides'), ('minutes', 'Minutes'),
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
                'agenda': 'list-columns', 'slides': 'easel', 'minutes': 'journal-text',
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


class TrainingCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Training categories'

    def __str__(self):
        return self.name


class TrainingVideo(models.Model):
    sha256 = models.CharField(max_length=64, blank=True, editable=False)
    """Training resources use the existing video table and file URLs."""
    class Kind(models.TextChoices):
        VIDEO = 'video', 'Video'
        GUIDE = 'guide', 'PDF guide'
        LINK = 'link', 'External link'

    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    category = models.ForeignKey(TrainingCategory, on_delete=models.SET_NULL, blank=True, null=True)
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.VIDEO, db_default=Kind.VIDEO)
    external_url = models.URLField(max_length=2000, blank=True, db_default='',
                                   validators=[URLValidator(schemes=['https'])])
    published = models.BooleanField(default=False)
    filename = models.CharField(max_length=255, blank=True, editable=False)
    file_size = models.PositiveBigIntegerField(default=0, editable=False)
    content_type = models.CharField(max_length=100, blank=True, editable=False)
    file_version = models.CharField(max_length=32, blank=True, editable=False)

    class Meta:
        ordering = ['title', 'pk']
        verbose_name = 'training resource'

    def __str__(self):
        return self.title


class TrainingVideoChunk(models.Model):
    resource = models.ForeignKey(TrainingVideo, on_delete=models.CASCADE, related_name='file_chunks')
    position = models.PositiveIntegerField()
    content = models.BinaryField()

    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['resource', 'position'], name='fgtida_training_chunk_position')]


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
    source_metadata = models.JSONField(default=dict, blank=True, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['document','revision'],name='fgtida_version_revision_unique'),
            models.UniqueConstraint(fields=['document','version'],name='fgtida_version_label_unique')]


class DocumentVersionAlias(models.Model):
    document = models.ForeignKey('doc.Document', on_delete=models.CASCADE)
    version = models.CharField(max_length=32)
    target = models.ForeignKey(DocumentVersion, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['document', 'version'], name='fgtida_version_alias_unique')]


class PublicDocumentType(models.Model):
    CONTRIBUTION = 'Contribution'
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'public document type'

    def __str__(self):
        return self.name


class DocumentAttribution(models.Model):
    """Optional editor override for the credits displayed on a document."""
    document = models.OneToOneField('doc.Document', on_delete=models.CASCADE, related_name='tida_attribution')
    authors = models.TextField('Author(s)', blank=True, help_text='One author per line. Your edits override imported metadata, including an explicitly cleared value.')
    affiliations = models.TextField(blank=True, help_text='One organization per line.')
    contacts = models.TextField(blank=True, help_text='One public contact name per line.')
    contact_emails = models.TextField(blank=True, help_text='One public contact email per line. Addresses entered here appear on the public document page.')
    credit_provenance = models.JSONField(default=dict, editable=False,
        help_text='Tracks automatic values and deliberate editor overrides independently.')
    public_type = models.ForeignKey(PublicDocumentType, null=True, blank=True, on_delete=models.SET_NULL,
        help_text='Choose the type shown publicly. Leave blank to use the default from the document’s workflow.')
    additional_types = models.ManyToManyField(PublicDocumentType, blank=True, related_name='additional_attributions',
        help_text='Additional document types. A use case can also be a Contribution.')


class DocumentListing(models.Model):
    """Editorial classification of an input/output, using the native archive."""
    document = models.OneToOneField('doc.Document', on_delete=models.CASCADE, related_name='tida_listing')
    direction = models.CharField(max_length=6, choices=[('input', 'Input document'), ('output', 'Output document')], db_index=True)
    meeting = models.ForeignKey('meeting.Meeting', blank=True, null=True, on_delete=models.SET_NULL)
    source = models.CharField(max_length=255, blank=True, help_text='Submitting organization, author or working group.')
    created = models.DateTimeField(default=timezone.now, db_index=True)
    posted_date = models.DateField(null=True, blank=True, help_text='Liaison submission date. Blank uses the recorded creation date.')
    destination = models.CharField('To', max_length=500, blank=True)
    deadline = models.DateField(null=True, blank=True)
    purpose = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True, help_text='Optional public liaison message. The original document and attachments remain on its document page.')


class DocumentFormatManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().defer('content')


class DocumentFormat(models.Model):
    """Optional editorial Word/PDF alternatives belonging to one archive revision."""
    document = models.ForeignKey('doc.Document', on_delete=models.CASCADE, related_name='tida_formats')
    revision = models.CharField(max_length=16)
    kind = models.CharField(max_length=4, choices=[('pdf', 'PDF'), ('docx', 'Word'), ('zip', 'RST ZIP package')])
    filename = models.CharField(max_length=255, editable=False)
    content = models.BinaryField(editable=False)
    sha256 = models.CharField(max_length=64, blank=True, editable=False)
    is_public = models.BooleanField(default=True, editable=False)
    updated = models.DateTimeField(auto_now=True)
    objects = DocumentFormatManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['document', 'revision', 'kind'], name='fgtida_document_format_unique')]


class ContributionRevisionOriginal(models.Model):
    """Private unmodified ZIP, separate from all publicly served alternatives."""
    contribution = models.ForeignKey(Contribution, on_delete=models.CASCADE, related_name='revision_originals')
    revision = models.CharField(max_length=16)
    filename = models.CharField(max_length=255)
    content = models.BinaryField(editable=False)
    sha256 = models.CharField(max_length=64)
    created = models.DateTimeField(auto_now_add=True)
    objects = DocumentFormatManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['contribution', 'revision'], name='fgtida_revision_original_unique')]


class FileScan(models.Model):
    sha256 = models.CharField(max_length=64, primary_key=True)
    filename = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    status = models.CharField(max_length=12, default='pending', choices=[
        ('pending', 'Quarantined'), ('clean', 'Passed checks'), ('rejected', 'Malware rejected'),
        ('blocked', 'File limits or unsupported media'), ('error', 'Scanner unavailable'),
        ('admin', 'Administrator trusted — antivirus skipped')])
    created = models.DateTimeField(auto_now_add=True)
    checked = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    engine = models.CharField(max_length=255, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    formats = models.JSONField(default=list, blank=True)
    rejected_formats = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-created']


class FileScanEvent(models.Model):
    scan = models.ForeignKey(FileScan, on_delete=models.PROTECT, related_name='events')
    created = models.DateTimeField(auto_now_add=True)
    operation = models.CharField(max_length=16)
    status = models.CharField(max_length=12)
    format = models.CharField(max_length=16, blank=True)
    engine = models.CharField(max_length=255, blank=True)
    detail = models.CharField(max_length=500, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-created', '-pk']


class FileScanException(models.Model):
    scan = models.ForeignKey(FileScan, on_delete=models.PROTECT, related_name='exceptions')
    format = models.CharField(max_length=16)
    detection = models.CharField(max_length=255)
    reason = models.CharField(max_length=400)
    approved_at = models.DateTimeField(auto_now=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['scan', 'format'], name='fgtida_scan_exception_format_unique')]


class MeetingSubmissionWindow(models.Model):
    meeting = models.OneToOneField('meeting.Meeting', on_delete=models.CASCADE, related_name='submission_window')
    accepting_submissions = models.BooleanField(default=True)
    deadline = models.DateTimeField(null=True, blank=True, help_text='UTC. Blank closes at the start of the meeting.')
    reopen_until = models.DateTimeField(null=True, blank=True, help_text='UTC. Extend the deadline to this date; enable Accepting submissions to reopen.')

    def __str__(self):
        return f'Submissions for {self.meeting.display_title}'


class MeetingParticipant(models.Model):
    meeting = models.ForeignKey('meeting.Meeting', on_delete=models.CASCADE, related_name='tida_participants')
    full_name = models.CharField(max_length=511, blank=True, help_text='Use the supplied full name without guessing its given/family-name order.')
    first_name = models.CharField(max_length=255, blank=True)
    last_name = models.CharField(max_length=255, blank=True)
    affiliation = models.CharField(max_length=255, blank=True, verbose_name='Organization / affiliation')
    country = models.CharField(max_length=100, blank=True, verbose_name='Country / territory')
    attendance = models.CharField(max_length=8, blank=True, default='', choices=[('onsite', 'Onsite'), ('remote', 'Online')])
    published = models.BooleanField(default=False, help_text='Publish only participant data authorized for the public list.')

    class Meta:
        ordering = ['last_name', 'first_name', 'pk']

    def clean(self):
        from django.core.exceptions import ValidationError
        if not (self.full_name.strip() or self.first_name.strip() or self.last_name.strip()):
            raise ValidationError({'full_name': 'Enter a full name or given/family name.'})
        if self.meeting_id and self.meeting.type_id != 'tida':
            raise ValidationError({'meeting': 'Select an FG-TIDA meeting.'})

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.full_name.strip() or f'{self.first_name} {self.last_name}'.strip()
