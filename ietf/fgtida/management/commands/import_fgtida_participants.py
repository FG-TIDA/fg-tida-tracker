"""Import only the explicitly public fields of a meeting participant CSV."""
import csv
import unicodedata
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ietf.fgtida import core
from ietf.fgtida.models import MeetingParticipant


def normalized(value):
    return ' '.join(unicodedata.normalize('NFC', value or '').split())


def identity(name, affiliation, country):
    return tuple(normalized(value).casefold() for value in (name, affiliation, country))


class Command(BaseCommand):
    help = 'Preview/import a participant CSV without accounts, emails, invented attendance or duplicate rows.'

    def add_arguments(self, parser):
        parser.add_argument('--meeting', required=True)
        parser.add_argument('--csv', required=True, dest='csv_path')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--publish', action='store_true', help='Publish the authorized name, organization and country fields.')

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options['csv_path'])
        if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            raise CommandError('Select a UTF-8 CSV file of at most 5 MB.')
        try:
            with path.open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream)
                columns = {normalized(label).casefold(): label for label in reader.fieldnames or []}
                names = columns.get('full name')
                organizations = columns.get('organization') or columns.get('organitzation') or columns.get('organisation')
                countries = columns.get('country') or columns.get('country/territory')
                if not all((names, organizations, countries)):
                    raise CommandError('CSV requires Full Name, Organization (or Organitzation) and Country columns.')
                rows, duplicates, seen = [], 0, set()
                for number, row in enumerate(reader, 2):
                    if not any(value for value in row.values()):
                        continue
                    values = tuple(normalized(row.get(key)) for key in (names, organizations, countries))
                    if not values[0] or any(len(value) > maximum for value, maximum in zip(values, (511, 255, 100))):
                        raise CommandError(f'Invalid or overlong public field in CSV row {number}. No rows imported.')
                    key = identity(*values)
                    if key in seen:
                        duplicates += 1
                        continue
                    seen.add(key)
                    rows.append(values)
        except (UnicodeError, csv.Error) as error:
            raise CommandError('Cannot read this UTF-8 CSV. No participant data was logged.') from error
        try:
            meeting = core.meetings().select_for_update().get(number=options['meeting'])
        except core.meetings().model.DoesNotExist:
            raise CommandError('Select an existing FG-TIDA meeting.') from None
        current = {}
        for record in MeetingParticipant.objects.filter(meeting=meeting):
            key = identity(record.display_name, record.affiliation, record.country)
            if key in current:
                raise CommandError('The current participant list has duplicate identities. Review it in admin before importing.')
            current[key] = record
        created = matched = published = 0
        for name, affiliation, country in rows:
            record = current.get(identity(name, affiliation, country))
            if record:
                matched += 1
                if options['apply'] and options['publish'] and not record.published:
                    record.published = True
                    record.save(update_fields=['published'])
                    published += 1
            else:
                created += 1
                if options['apply']:
                    MeetingParticipant.objects.create(meeting=meeting, full_name=name, affiliation=affiliation,
                        country=country, attendance='', published=options['publish'])
        self.stdout.write(f'{"Applied" if options["apply"] else "Plan only"}: {len(rows)} distinct participants; '
            f'{duplicates} duplicate CSV rows skipped; {created} new; {matched} already present; '
            f'{published} existing records newly published. Attendance retained when known; otherwise blank. '
            'No accounts or emails created. No participant names were written to this log.')
