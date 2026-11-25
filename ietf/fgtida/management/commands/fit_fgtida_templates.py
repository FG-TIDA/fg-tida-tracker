import json
from collections import Counter
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from ietf.fgtida.models import Contribution
from ietf.fgtida.template_repairs import prepare, apply_fit


class Command(BaseCommand):
    help = 'Plan template adaptations of existing NWI sources, preserving originals. No emails or GitHub writes.'

    def add_arguments(self, parser):
        parser.add_argument('--contribution', type=int)
        parser.add_argument('--draft-dir', help='Optional private directory for review ZIPs; never use a public media directory.')
        parser.add_argument('--apply', action='store_true', help='Archive only complete adaptations as new versions.')
        parser.add_argument('--reason', default='')

    def handle(self, *args, **options):
        if options['apply'] and len(options['reason'].strip()) < 10:
            raise CommandError('--apply requires --reason of at least 10 characters.')
        output = Path(options['draft_dir']) if options['draft_dir'] else None
        if output:
            from django.conf import settings
            public = Path(settings.MEDIA_ROOT).resolve() if settings.MEDIA_ROOT else None
            if public and output.resolve().is_relative_to(public):
                raise CommandError('Review drafts must be outside public media storage.')
            output.mkdir(parents=True, exist_ok=True, mode=0o700)
        records = Contribution.objects.select_related('document').order_by('pk')
        if options['contribution']:
            records = records.filter(pk=options['contribution'])
        counts = Counter()
        for item in records.iterator(chunk_size=1):
            try:
                report, archive, previous = prepare(item)
                if output and archive:
                    # Only stable database IDs in names; no contacts or source titles in logs.
                    path = output / f'contribution-{item.pk}-template-review.zip'
                    with path.open('xb') as handle:
                        handle.write(archive)
                    path.chmod(0o600)
                if options['apply'] and report['state'] == 'ready':
                    apply_fit(item.pk, report, archive, previous, options['reason'])
                    report['state'] = 'archived'
            except (ValidationError, ValueError, OSError) as error:
                report = {'state': 'manual_review', 'issues': error.messages if isinstance(error, ValidationError) else [str(error)]}
            counts[report['state']] += 1
            self.stdout.write(json.dumps({'contribution': item.pk, **report}))
        self.stdout.write(json.dumps({'summary': dict(counts)}))
        self.stdout.write('Original uploads and earlier versions retained. Missing information requires editorial review. No mail or GitHub writes.')
