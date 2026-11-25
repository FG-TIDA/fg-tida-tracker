import json
import csv
import os
from collections import Counter
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from ietf.fgtida.models import Contribution
from ietf.fgtida.template_repairs import prepare, apply_fit, source_managed


CONTACT_FIELDS = ['contribution_id', 'reference', 'title', 'document_url', 'submission_method',
                  'template_status', 'submitter_name', 'submitter_email', 'submission_contact_name',
                  'submission_contact_email', 'issues', 'author_review_pending']


def contact_row(item, report):
    from django.conf import settings
    user = item.submitter
    email = user.email if user else ''
    if user and not email:
        from ietf.person.models import Email
        email = Email.objects.filter(person__user_id=user.pk, active=True).order_by('pk').values_list('address', flat=True).first() or ''
    domain = getattr(settings, 'FGTIDA_PUBLIC_DOMAIN', '')
    site = 'https://' + domain if domain else getattr(settings, 'SITE_URL', '').rstrip('/')
    return dict(zip(CONTACT_FIELDS, [item.pk, item.reference, item.title,
        site + '/doc/' + item.document.name + '/' if item.document_id else '', item.submission_method,
        report['state'], user.get_full_name() if user else '', email, item.contact_name, item.contact_email,
        '; '.join(dict.fromkeys([*report.get('original_issues', []), *report.get('issues', [])])),
        report['state'] in {'ready', 'archived', 'archived_provisional', 'awaiting_author_review', 'needs_review'}]))


def spreadsheet_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


class Command(BaseCommand):
    help = 'Plan template adaptations of existing NWI sources, preserving originals. No emails or GitHub writes.'

    def add_arguments(self, parser):
        parser.add_argument('--contribution', type=int)
        parser.add_argument('--draft-dir', help='Optional private directory for review ZIPs; never use a public media directory.')
        parser.add_argument('--apply', action='store_true', help='Archive only complete adaptations as new versions.')
        parser.add_argument('--reason', default='')
        parser.add_argument('--allow-incomplete-legacy', action='store_true',
            help='With --apply, archive incomplete legacy adaptations with explicit missing-data markers and an author-review notice. New submissions remain strict.')
        parser.add_argument('--contacts-csv', help='Private CSV of submitters/contact details for records needing review. Never commit or publish it.')

    def handle(self, *args, **options):
        if options['apply'] and len(options['reason'].strip()) < 10:
            raise CommandError('--apply requires --reason of at least 10 characters.')
        if options['allow_incomplete_legacy'] and not options['apply']:
            raise CommandError('--allow-incomplete-legacy requires --apply.')
        contacts_path = Path(options['contacts_csv']) if options['contacts_csv'] else None
        if contacts_path:
            from django.conf import settings
            public = Path(settings.MEDIA_ROOT).resolve() if settings.MEDIA_ROOT else None
            if public and contacts_path.resolve().is_relative_to(public):
                raise CommandError('Contact reports must be outside public media storage.')
            if contacts_path.exists():
                raise CommandError('Contact report already exists; choose a new private filename.')
        output = Path(options['draft_dir']) if options['draft_dir'] else None
        if output:
            from django.conf import settings
            public = Path(settings.MEDIA_ROOT).resolve() if settings.MEDIA_ROOT else None
            if public and output.resolve().is_relative_to(public):
                raise CommandError('Review drafts must be outside public media storage.')
            output.mkdir(parents=True, exist_ok=True, mode=0o700)
        records = Contribution.objects.select_related('document', 'submitter').order_by('pk')
        if options['contribution']:
            records = records.filter(pk=options['contribution'])
        counts = Counter()
        contact_file = None
        try:
            if contacts_path:
                descriptor = os.open(contacts_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                contact_file = os.fdopen(descriptor, 'w', encoding='utf-8-sig', newline='')
                writer = csv.DictWriter(contact_file, fieldnames=CONTACT_FIELDS)
                writer.writeheader()
            for item in records.iterator(chunk_size=1):
                try:
                    if item.status == 'catalogued' or (item.document_id and source_managed(item, item.document)):
                        report = {'state': 'source_managed', 'issues': ['Use a reviewed source-repository change; no tracker override was made.']}
                        archive = previous = None
                    else:
                        report, archive, previous = prepare(item)
                    if output and archive:
                        path = output / f'contribution-{item.pk}-template-review.zip'
                        with path.open('xb') as handle:
                            handle.write(archive)
                        path.chmod(0o600)
                    provisional = report['state'] == 'needs_review' and options['allow_incomplete_legacy']
                    if options['apply'] and (report['state'] == 'ready' or provisional):
                        apply_fit(item.pk, report, archive, previous, options['reason'],
                                  allow_incomplete_legacy=options['allow_incomplete_legacy'])
                        report.update(state='archived_provisional' if provisional else 'archived', provisional=provisional)
                except (ValidationError, ValueError, OSError) as error:
                    report = {'state': 'manual_review', 'issues': error.messages if isinstance(error, ValidationError) else [str(error)]}
                if contact_file and report['state'] not in {'already_conforming', 'source_managed'}:
                    writer.writerow({key: spreadsheet_cell(value) for key, value in contact_row(item, report).items()})
                    contact_file.flush()
                counts[report['state']] += 1
                self.stdout.write(json.dumps({'contribution': item.pk, **report}))
        finally:
            if contact_file:
                contact_file.close()
        self.stdout.write(json.dumps({'summary': dict(counts)}))
        self.stdout.write('Original uploads and earlier versions retained. Missing information requires editorial review. No mail or GitHub writes.')
        if contacts_path:
            self.stdout.write('Private contact CSV created. Keep it outside GitHub, public downloads and shared logs.')
