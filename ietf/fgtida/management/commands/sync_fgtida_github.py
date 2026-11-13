from collections import Counter
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.core.exceptions import ValidationError
from ietf.fgtida.models import GitHubRepository, WorkingGroupConfiguration
from ietf.fgtida.github_client import GitHubClient
from ietf.fgtida.github_catalog import sync_repository


class Command(BaseCommand):
    help = 'Plan or apply document synchronization from explicitly enabled GitHub repositories.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write changes; default is a read-only plan.')
        parser.add_argument('--repository', help='Limit to owner/repository.')
        parser.add_argument('--discover', action='store_true', help='Discover WG-numbered repositories inside administrator-configured WG organizations.')

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(746432, 2)')
            acquired=cursor.fetchone()[0]
        if not acquired:
            self.stdout.write('Another catalog sync is running; skipped.')
            return
        try:
            self.synchronize(options)
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(746432, 2)')

    def synchronize(self, options):
        if options['discover']:
            import re
            for group in WorkingGroupConfiguration.objects.filter(github_account__enabled=True).select_related('github_account','group'):
                client=GitHubClient(group.github_account)
                for repo in client.pages('/orgs/'+group.github_account.organization+'/repos?type=public'):
                    if repo.get('archived') or repo.get('fork') or repo.get('private') or not re.fullmatch(r'wg'+str(group.number)+r'-[a-z0-9-]+',repo['name']):
                        continue
                    if GitHubRepository.objects.filter(account=group.github_account,name=repo['name']).exists():
                        continue
                    self.stdout.write('Discovered '+repo['full_name'])
                    if options['apply']:
                        charter = repo['name'].endswith('-charter')
                        paths = ['src/doc/index.rst']
                        if charter:
                            tree = client.request('GET', '/repos/'+repo['full_name']+'/git/trees/'+repo['default_branch']+'?recursive=1')
                            if not tree or tree.get('truncated'):
                                raise CommandError('Cannot determine charter document path for '+repo['full_name'])
                            available = {row['path'] for row in tree.get('tree', []) if row['type'] == 'blob'}
                            paths = [next((path for path in ['charter.md', 'src/doc/index.rst'] if path in available), '')]
                            if not paths[0]:
                                self.stdout.write('No supported charter entry document; configure manually: '+repo['full_name'])
                                continue
                        GitHubRepository.objects.create(account=group.github_account,name=repo['name'],github_id=repo['id'],
                            group=group.group,branch=repo['default_branch'],mode='files' if charter else 'workitem',paths=paths,
                            document_type='charter' if charter else 'tida', group_evidence=repo['html_url'])
        records = GitHubRepository.objects.filter(enabled=True, account__enabled=True).select_related('account','group')
        if options['repository']:
            owner, name = options['repository'].split('/',1)
            records = records.filter(account__organization__iexact=owner, name=name)
        for repository in records:
            try:
                client = GitHubClient(repository.account)
                metadata = client.request('GET', '/repos/'+repository.full_name)
                if not metadata or metadata.get('private'):
                    raise ValueError('Only public repositories are imported into the public catalog.')
                if repository.github_id and repository.github_id != metadata['id']:
                    raise ValueError('Repository identity changed; administrator review is required.')
                if options['apply'] and not repository.github_id:
                    repository.github_id = metadata['id']
                    repository.save(update_fields=['github_id'])
                result = Counter(sync_repository(repository, dry_run=not options['apply'], client=client,metadata=metadata))
                self.stdout.write(f'{repository}: {dict(result)}')
            except Exception as error:
                # Never print tokens, request bodies or upstream response contents.
                detail='; '.join(error.messages) if isinstance(error,ValidationError) else 'Check source configuration, GitHub permissions or rate limits.'
                raise CommandError(f'Synchronization stopped for {repository}. {type(error).__name__}: {detail}') from None
