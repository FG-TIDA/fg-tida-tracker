"""Read-only candidates by default; explicitly consolidate a reviewed pair."""
import hashlib
import json
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction
from ietf.fgtida.models import Contribution, ContributionRevisionOriginal
from ietf.fgtida.contribution_duplicates import title_key
from ietf.fgtida import core


def relabel_history(data, version, entry):
    from ietf.fgtida.source_packages import unpack, pack, prepare_zip
    from ietf.fgtida.submission_template import sections
    from ietf.fgtida.source_versions import declared_version
    files = unpack(data)
    for name, content in files.items():
        if name != entry or not name.endswith('.rst'):
            continue
        text = content.decode('utf-8-sig')
        headings, _ = sections(text)
        history = next((start for start, label in headings if label == 'History'), None)
        if history is None or not declared_version(text):
            continue
        lines = text.splitlines(keepends=True)
        # Keep the author's entire History table and technical text. Add only
        # the explicit editorial archive version; the old ZIP remains intact.
        lines.insert(history + 2, f'\nVersion: {version}\n\nEditorial consolidation archive. The original submitted version labels and package are retained in the duplicate record history. This is not approval.\n\n')
        files[name] = ''.join(lines).encode('utf-8')
        return prepare_zip(pack(files), name, complete=True)
    raise ValidationError('No unambiguous RST History found. Consolidation refused; review the source manually.')


class Command(BaseCommand):
    help = 'Report possible duplicates without contact data, or preserve and consolidate an explicitly reviewed pair.'

    def add_arguments(self, parser):
        parser.add_argument('--primary', type=int)
        parser.add_argument('--duplicate', type=int)
        parser.add_argument('--archive-version')
        parser.add_argument('--reason', default='')
        parser.add_argument('--apply', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        records = Contribution.objects.select_related('document').defer('content', 'source_archive')
        if not options['primary'] and not options['duplicate']:
            if options['apply']:
                raise CommandError('Choose a specific primary and duplicate. Bulk automatic merging is not supported.')
            rows = list(records.filter(duplicate_of__isnull=True))
            pairs = []
            for index, first in enumerate(rows):
                for second in rows[index + 1:]:
                    a, b = title_key(first.title), title_key(second.title)
                    if first.group_id == second.group_id and (a == b or min(len(a), len(b)) >= 40 and (a.startswith(b + ' ') or b.startswith(a + ' '))):
                        pairs.append({'records': [first.pk, second.pk], 'references': [first.reference, second.reference], 'titles': [first.title, second.title], 'action': 'Review originals and source identities; matching titles are not proof.'})
            self.stdout.write(json.dumps({'mode': 'read-only', 'candidate_pairs': pairs}, indent=2))
            return
        if not all(options[key] for key in ('primary', 'duplicate')) or options['primary'] == options['duplicate']:
            raise CommandError('Select two different contribution IDs.')
        try:
            pair = {row.pk: row for row in records.select_for_update(of=('self',)).filter(pk__in=[options['primary'], options['duplicate']])}
            primary, duplicate = pair[options['primary']], pair[options['duplicate']]
        except KeyError:
            raise CommandError('Both contribution IDs must exist.') from None
        if duplicate.duplicate_of_id == primary.pk:
            self.stdout.write('Already consolidated. No changes made.')
            return
        if primary.duplicate_of_id or duplicate.duplicate_of_id or primary.group_id != duplicate.group_id:
            raise CommandError('Choose active records in the same group; chained merges are not allowed.')
        if primary.status != 'pending' or duplicate.status != 'pending' or not primary.document_id or not duplicate.document_id:
            raise CommandError('Only submitted uploaded contributions can be consolidated here. GitHub or adopted records require source/workflow review.')
        from ietf.fgtida.work_items import state_of
        if state_of(primary.document) != 'submitted' or state_of(duplicate.document) != 'submitted':
            raise CommandError('Both documents must be in Submitted state. No approval or review decision can be overwritten.')
        from ietf.fgtida.template_repairs import source_managed
        if source_managed(primary, primary.document) or source_managed(duplicate, duplicate.document):
            raise CommandError('Source-managed records require repository review; no GitHub content was changed.')
        from ietf.fgtida.versioning import next_version
        try:
            version = next_version(primary.document, options['archive_version'])
        except ValidationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps({'mode': 'apply' if options['apply'] else 'plan', 'primary': primary.pk,
            'duplicate': duplicate.pk, 'new_editorial_archive_version': version,
            'preserves': 'Both original records, original bytes, timestamps, links and prior versions. No emails or GitHub writes.'}, indent=2))
        if not options['apply']:
            return
        if len(options['reason'].strip()) < 15:
            raise CommandError('Give a substantive --reason documenting the reviewed identity and author intent.')
        from ietf.fgtida.file_safety import preview_source, require_clean
        data, filename = preview_source(duplicate.document)
        if not data or not filename.endswith('.zip'):
            raise CommandError('The duplicate needs a maintained RST ZIP before consolidation.')
        try:
            require_clean(data, filename)
            archive, manifest = relabel_history(data, version, duplicate.source_manifest.get('entry_document', 'index.rst'))
            from ietf.fgtida.submission_template import check_archive
            check_archive(archive, manifest)
            from ietf.fgtida.github_attribution import source_attribution
            from ietf.fgtida.source_packages import package_text
            document = primary.document
            note = f'Consolidated submission record {duplicate.pk} into {primary.pk}. Editorial archive version {version}; original author version and submission timestamp retained in source record. {options["reason"].strip()}'
            core.save_document(document, core.actor_for(), note, data=archive, filename='source.zip', version=version,
                source_metadata={**source_attribution(package_text(archive)), 'consolidated_from': duplicate.pk,
                    'original_recorded': duplicate.created.isoformat(), 'original_file_sha256': hashlib.sha256(data).hexdigest()})
        except ValidationError as error:
            raise CommandError(str(error)) from error
        ContributionRevisionOriginal.objects.create(contribution=primary, revision=document.rev,
            filename='original-resubmission.zip', content=data, sha256=hashlib.sha256(data).hexdigest())
        primary.source_archive = archive
        primary.source_manifest = {**manifest, 'method': 'rst', 'consolidated_from': duplicate.pk}
        primary.save(update_fields=['source_archive', 'source_manifest'])
        duplicate.duplicate_of = primary
        duplicate.review_note += '\n' + note
        duplicate.save(update_fields=['duplicate_of', 'review_note'])
        from ietf.doc.models import DocEvent
        DocEvent.objects.create(doc=duplicate.document, by=core.actor_for(), type='changed_document',
            desc=note, rev=duplicate.document.rev)
        from ietf.fgtida.models import OutgoingMail
        from django.utils import timezone
        OutgoingMail.objects.filter(contribution=duplicate, sent__isnull=True).update(expires=timezone.now(), last_error='Duplicate consolidated; unsent notification cancelled.')
        self.stdout.write('Consolidated. Duplicate catalogue entry suppressed, current-page link redirects, original revision downloads remain retained. No messages sent.')
