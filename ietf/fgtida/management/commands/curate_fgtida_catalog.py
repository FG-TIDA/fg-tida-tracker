"""One-time, reversible correction of the former repository-file import."""
import json
from pathlib import Path, PurePosixPath
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify
from ietf.doc.models import Document, State
from ietf.fgtida import core


def is_support_file(repository, path):
    filename = PurePosixPath(path).name.lower()
    return (repository in ('spec-template', 'wg-charter-template')
            or path.startswith('use-case-register/')
            or filename in ('readme.md', 'changelog.md', 'howto.md', 'requirements.txt',
                            'wg-onboarding.md', 'minutes-template.md', 'charter-template.md',
                            'license', 'license.md', 'contributing.md', 'code_of_conduct.md'))


class Command(BaseCommand):
    help = 'Review or apply catalog corrections using the preserved local import manifest; no network access.'

    def add_arguments(self, parser):
        parser.add_argument('--archive', required=True)
        parser.add_argument('--apply', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        records = json.loads(Path(options['archive']).read_text(encoding='utf-8'))
        excluded = []
        for entry in records:
            if entry['model'] != 'tida.resource':
                continue
            old = entry['fields']
            if not is_support_file(old['repository'], old['source_path']):
                continue
            name = f"fg-tida-{slugify(old['repository'] or 'document')}-{slugify(old['title'])[:150]}-{entry['pk']}"[:255]
            doc = Document.objects.filter(name=name).first()
            if doc:
                excluded.append(doc)
        self.stdout.write(f'{len(excluded)} repository support records to exclude; two canonical templates to configure.')
        for doc in excluded:
            self.stdout.write(doc.name)
        if not options['apply']:
            return
        call_command('configure_fgtida', skip_checks=True, stdout=self.stdout)
        actor = core.actor_for()
        for doc in excluded:
            if not doc.tags.filter(slug='tida-uncatalogued').exists():
                doc.tags.add('tida-uncatalogued')
                core.save_document(doc, actor, 'Excluded repository support file from the public FG-TIDA catalog. Original record and revisions retained.')
        for name, title, repository, description in [
            ('fg-tida-spec-template', 'Specification template', 'spec-template', 'Template package for drafting an FG-TIDA specification.'),
            ('fg-tida-charter-template', 'Working group charter template', 'wg-charter-template', 'Template package for drafting an FG-TIDA working group charter.'),
        ]:
            doc, created = Document.objects.get_or_create(name=name, defaults={
                'title': title, 'type_id': 'tida-template', 'group': core.groups().get(acronym='tida'),
                'abstract': description, 'external_url': 'https://github.com/FG-TIDA/' + repository})
            if created:
                doc.tags.add('tida-templates')
                core.save_document(doc, actor, 'Registered the template package as one catalog item. Future revisions are managed by tracker administrators.',
                                   state=State.objects.get(type_id='tida-template', slug='active'))
        self.stdout.write(f'Public catalog now contains {core.documents().count()} records. No source files were deleted.')
