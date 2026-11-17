from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from ietf.fgtida.contributions import archive_github_revision
from ietf.fgtida.models import Contribution


class Command(BaseCommand):
    help = 'Archive a public GitHub commit on an existing pending contribution by its permanent document identifier.'

    def add_arguments(self, parser):
        parser.add_argument('document')
        parser.add_argument('version')
        parser.add_argument('repository')
        parser.add_argument('commit')
        parser.add_argument('--source-folder', default='src/doc')
        parser.add_argument('--entry-document', default='src/doc/index.rst')

    def handle(self, document, version, repository, commit, source_folder, entry_document, **options):
        try:
            item = Contribution.objects.only('pk').get(document__name=document)
            archive_github_revision(item.pk, None, repository, commit, source_folder, entry_document, version)
        except (Contribution.DoesNotExist, Contribution.MultipleObjectsReturned) as error:
            raise CommandError('The document must identify exactly one contribution.') from error
        except ValidationError as error:
            raise CommandError(' '.join(error.messages)) from error
        self.stdout.write(self.style.SUCCESS(f'Archived version {version} on /doc/{document}/.'))
