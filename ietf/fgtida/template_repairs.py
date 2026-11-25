"""Prepare reviewable template adaptations without inventing contribution content."""
import hashlib

from django.core.exceptions import ValidationError
from django.db import transaction

from .submission_template import PROFILE, SECTIONS, problems, sections


def source_managed(item, document):
    from .models import WorkItem
    return (item.status == 'catalogued' or document.github_sources.filter(sync_mode='automatic').exists()
            or WorkItem.objects.filter(document=document, repository__isnull=False).exists())


def fit_text(text, *, title, version, authors=(), affiliations=(), contacts=(), abstract='', working_group=''):
    headings, blocks = sections(text)
    lines = text.splitlines(keepends=True)
    prefix = ''.join(lines[:headings[0][0]]) if headings else ''
    retained = '' if headings else text
    for position, (start, label) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        if label not in SECTIONS:
            # Replace only the first document heading, retaining its contents.
            is_title = position == 0 and label.casefold() == title.strip().casefold()
            retained += ''.join(lines[start + 2:end] if is_title else lines[start:end])
    missing = 'Not supplied — administrator review required.'
    defaults = {
        'Abstract': abstract.strip() or missing,
        'History': (f'Version: {version}\n\nDocument: {title}\n\nApproval: Not recorded. Template adaptation is not approval.'
                    '\n\nFocus Group: TIDA\n\nWorking Group / stream: ' + (working_group or 'Not specified in original submission.')),
        'Authors': (', '.join(authors) + ('\n\nAffiliation(s): ' + ', '.join(affiliations) if affiliations else '')
                    + ('\n\nContact: ' + ', '.join(contacts) if contacts else '')) if authors else missing,
        'Keywords': missing,
        'Foreword': ('This contribution is submitted to the ITU-T Focus Group on Trust and Identity '
                     'for Humans and Agentic AI (FG-TIDA). Publication in the tracker records a '
                     'submission and does not imply endorsement or approval by the Focus Group. '
                     'This paragraph provides tracker context; the technical content below is retained from the source.'),
    }
    output = title + '\n' + '=' * max(3, len(title)) + '\n\n'
    for label in SECTIONS:
        body = '\n\n'.join(blocks.get(label, [])) or defaults[label]
        if label == 'History' and blocks.get(label):
            body += ('\n\nTemplate adaptation (original history retained):\n\n'
                     + defaults['History'])
        output += label + '\n' + '-' * len(label) + '\n\n' + body + '\n\n'
    if retained.strip() and not sections(retained)[0]:
        output += 'Contribution content\n--------------------\n\n'
    output += retained
    if prefix.strip():
        # Preserve old cover material and assets, but do not place logos and
        # legacy cover tables ahead of the specification's front matter.
        output += '\n\nOriginal front matter\n---------------------\n\n' + prefix
    return output


def prepare(item):
    from .file_safety import allowed, preview_source
    from .source_packages import unpack, pack, convert_word, prepare_zip
    from .word_scaffold import complete_rst_source
    from .github_attribution import source_attribution
    from .credits import resolve_credits
    from .versioning import next_version

    if not item.document_id:
        raise ValidationError('No linked public document; review the original upload individually.')
    document = item.document
    if source_managed(item, document):
        raise ValidationError('GitHub-managed source: adapt through a reviewed source pull request, not a tracker override.')
    previous, filename = preview_source(document)
    if not previous or not allowed(previous, filename):
        raise ValidationError('Current file missing or awaiting security checks; no security status was bypassed.')
    if filename.lower().endswith('.docx'):
        archive, manifest = convert_word(previous)
    elif filename.lower().endswith('.zip'):
        archive, manifest = previous, dict(item.source_manifest)
    else:
        raise ValidationError('Only Word and RST ZIP sources can be fitted automatically.')
    files = unpack(archive)
    entry = manifest.get('entry_document', '')
    if 'chapters/01_contribution.rst' in files and manifest.get('package_profile', '').startswith('word-'):
        entry = 'chapters/01_contribution.rst'
    if not entry:
        candidates = [name for name in files if name.endswith('.rst')]
        entry = 'index.rst' if 'index.rst' in files else candidates[0] if len(candidates) == 1 else ''
    if entry not in files:
        raise ValidationError('Specify the current RST entry document before adapting this source.')
    text = files[entry].decode('utf-8-sig')
    current = document.tida_versions.filter(revision=document.rev).first()
    adaptation = current.source_metadata.get('template_adaptation', {}) if current else {}
    if adaptation.get('profile') == PROFILE and adaptation.get('author_review_pending'):
        return {'state': 'awaiting_author_review', 'issues': adaptation.get('issues', []),
                'original_issues': adaptation.get('original_issues', []),
                'provisional': adaptation.get('provisional', False)}, None, previous
    from .submission_template import check_archive
    try:
        check_archive(archive, {'entry_document': entry})
        original_issues = []
    except ValidationError as error:
        original_issues = error.messages
    if not original_issues:
        return {'state': 'already_conforming'}, None, previous
    sources = list(document.github_sources.all())
    override = getattr(document, 'tida_attribution', None)
    credits, _, _ = resolve_credits(text, override, current.source_metadata if current else {}, sources)
    authors = credits.get('authors') or source_attribution(text).get('authors') or ([item.contact_name] if item.contact_name else [])
    affiliations = credits.get('affiliations') or ([item.contact_affiliation] if item.contact_affiliation else [])
    contacts = credits.get('contact_emails') or ([item.contact_email] if item.contact_email else [])
    version = next_version(document)
    from .templatetags.fgtida import group_label
    adapted = fit_text(text, title=document.title, version=version, authors=authors,
                       affiliations=affiliations, contacts=contacts, abstract=document.abstract or item.abstract,
                       working_group=group_label(item.group) if item.group_id else '')
    files[entry] = adapted.encode('utf-8')
    files, _ = complete_rst_source(files, manifest.get('entry_document') or entry)
    archive, _ = prepare_zip(pack(files), manifest.get('entry_document') or entry)
    # Include optional chapters when deciding whether automatic application is
    # possible, using exactly the same validator as a new submission.
    from .submission_template import check_archive
    try:
        check_archive(archive, {'entry_document': entry})
        issues = []
    except ValidationError as error:
        issues = error.messages
    return {'state': 'needs_review' if issues else 'ready', 'issues': issues, 'original_issues': original_issues, 'entry_document': entry,
            'version': version, 'previous_sha256': hashlib.sha256(previous).hexdigest(), 'profile': PROFILE}, archive, previous


@transaction.atomic
def apply_fit(pk, report, archive, previous, reason, *, allow_incomplete_legacy=False):
    from . import core
    from .file_safety import allowed, preview_source, trust_admin_derivative
    from .models import Contribution, DocumentFormat
    from ietf.doc.models import Document
    from .source_packages import prepare_zip
    from .submission_template import check_archive
    provisional = report['state'] == 'needs_review' and allow_incomplete_legacy
    if len(reason.strip()) < 10 or (report['state'] != 'ready' and not provisional):
        raise ValidationError('Only a complete template adaptation can be archived; provide a review reason.')
    item = Contribution.objects.select_for_update().get(pk=pk)
    doc = Document.objects.select_for_update().get(pk=item.document_id)
    current, filename = preview_source(doc)
    if current != previous:
        raise ValidationError('The source changed during preparation; rerun the plan before applying.')
    if not allowed(current, filename):
        raise ValidationError('Source safety status changed; complete its security review before adapting it.')
    # Keep this check inside the lock too; an editor may have re-enabled sync.
    if source_managed(item, doc):
        raise ValidationError('GitHub-managed sources require a source pull request.')
    archive, manifest = prepare_zip(archive, item.source_manifest.get('entry_document') or report['entry_document'])
    if not provisional:
        check_archive(archive, {'entry_document': report['entry_document']})
    adaptation = {**report, 'provisional': provisional, 'author_review_pending': True}
    original_formats = list(doc.tida_formats.filter(revision=doc.rev))
    trust_admin_derivative(archive, 'source.zip', previous, filename)
    core.save_document(doc, core.actor_for(), 'Provisional specification template adaptation; author verification pending. ' + reason.strip(),
        data=archive, filename='source.zip', version=report['version'], source_metadata={'template_adaptation': adaptation})
    for original in original_formats:
        if original.kind != 'zip':
            DocumentFormat.objects.get_or_create(document=doc, revision=doc.rev, kind=original.kind,
                defaults={'filename': original.filename, 'content': bytes(original.content), 'sha256': original.sha256})
    if filename.lower().endswith('.docx'):
        DocumentFormat.objects.get_or_create(document=doc, revision=doc.rev, kind='docx', defaults={
            'filename': filename, 'content': previous, 'sha256': hashlib.sha256(previous).hexdigest()})
    item.source_archive = archive
    item.source_manifest = {**item.source_manifest, **manifest, 'template_adaptation': adaptation}
    item.conversion_verified_at = None
    item.conversion_verified_by = None
    item.save(update_fields=['source_archive', 'source_manifest', 'conversion_verified_at', 'conversion_verified_by'])
