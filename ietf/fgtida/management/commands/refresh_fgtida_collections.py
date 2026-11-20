import json
from django.core.management.base import BaseCommand, CommandError
from ietf.fgtida.catalog_repairs import repair_catalog


class Command(BaseCommand):
    help = 'Refresh the authoritative themes/use-cases collections and repair explicit missing metadata; preview by default.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--no-sync', action='store_true', help='Repair existing local records without contacting GitHub.')

    def handle(self, *args, **options):
        failures = []
        if not options['no_sync']:
            from django.core.management import call_command
            for repository in ['fg-tida-wg0/use-cases', 'FG-TIDA/themes']:
                try:
                    call_command('sync_fgtida_github', repository=repository, apply=options['apply'], discover=False,
                                 skip_checks=True, stdout=self.stdout, stderr=self.stderr)
                except CommandError as error:
                    failures.append(repository)
                    self.stderr.write(str(error))
        stats = repair_catalog(apply=options['apply'])
        self.stdout.write(json.dumps({'mode': 'apply' if options['apply'] else 'plan', **dict(stats)}, indent=2))
        self.stdout.write('Only explicit document/source metadata was used. Unstated authors and affiliations were not invented. No GitHub writes or submission emails were sent.')
        if failures:
            raise CommandError('Review synchronization errors for: ' + ', '.join(failures))
