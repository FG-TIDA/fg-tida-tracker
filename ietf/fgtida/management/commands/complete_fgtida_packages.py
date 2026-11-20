import json
from collections import Counter
from django.db.models import Q
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from ietf.fgtida.models import Contribution
from ietf.fgtida.catalog_repairs import use_case_documents
from ietf.fgtida.contribution_packages import inspect_package, complete_package


class Command(BaseCommand):
    help = 'Plan or apply versioned repairs to incomplete contribution source packages; excludes imported use cases. No GitHub writes or emails.'

    def add_arguments(self, parser):
        parser.add_argument('--contribution', type=int)
        parser.add_argument('--word-only', action='store_true', help='Audit contributions with an original or alternative Word format.')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--reason', default='')

    def handle(self, *args, **options):
        if options['apply'] and len(options['reason'].strip()) < 10:
            raise CommandError('Applying package completion requires --reason (at least 10 characters).')
        records = Contribution.objects.exclude(document_id__in=use_case_documents().values('pk')).select_related('document')
        if options['contribution']:
            records = records.filter(pk=options['contribution'])
        if options['word_only']:
            records = records.filter(Q(filename__iendswith='.docx') | Q(filename__iendswith='.doc') |
                Q(document__tida_formats__kind__in=['docx', 'doc'])).distinct()
        errors = []
        counts = Counter()
        for item in records.order_by('pk').iterator(chunk_size=1):
            try:
                report = inspect_package(item)
                if options['apply'] and report['state'] == 'incomplete':
                    report['updated'] = complete_package(item.pk, reason=options['reason'])
                    item.refresh_from_db()
                    report['after_state'] = inspect_package(item)['state']
                counts[report.get('after_state', report['state'])] += 1
                self.stdout.write(json.dumps({'contribution': item.pk, **report}))
            except (ValidationError, ValueError) as error:
                errors.append(item.pk)
                self.stderr.write(f'Contribution {item.pk}: {error}')
        self.stdout.write(json.dumps({'summary': dict(counts), 'errors': errors}))
        self.stdout.write('Original uploads and earlier versions preserved. No identities were inferred, no emails sent and no GitHub changes made. Review completed packages before publication/approval.')
        if errors:
            raise CommandError('Review package errors for contribution IDs: ' + ', '.join(map(str, errors)))
