"""Explicit document discovery: no source code, HOWTOs or changelogs in the catalog."""
import fnmatch
import hashlib
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit, unquote
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from ietf.doc.models import Document, State
from ietf.name.models import DocTagName
from . import core
from .github_client import GitHubClient
from .models import GitHubSource, WorkItem

SUFFIXES = {'.md','.rst','.pdf','.pptx','.docx','.txt','.xml'}
SUPPORT = {'readme.md','changelog.md','howto.md','contributing.md','wg-onboarding.md','minutes-template.md','charter-template.md','license'}


def eligible(path, patterns):
    name = PurePosixPath(path)
    return (name.suffix.lower() in SUFFIXES and name.name.lower() not in SUPPORT
        and not any(part.startswith('.') for part in name.parts)
        and any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns))


def title_for(data, path):
    if PurePosixPath(path).suffix.lower() in {'.md','.rst','.txt'}:
        lines = data.decode('utf-8-sig').splitlines()
        for index, line in enumerate(lines[:70]):
            if line.startswith('# '):
                return line[2:].strip()[:255]
            if index+1 < len(lines) and line.strip() and re.fullmatch(r'[=\-~*]{3,}', lines[index+1].strip()):
                return line.strip()[:255]
    return PurePosixPath(path).stem.replace('_',' ')[:255]


@transaction.atomic
def store_source(repository, key, data, filename, title, source_url, sha, category=None, kind=None, dry_run=False):
    source = GitHubSource.objects.filter(repository=repository, source_key=key).first()
    digest = hashlib.sha256(data).hexdigest()
    if source and (source.sync_mode != 'automatic' or (not source.document_id and source.sha)):
        return 'manual/excluded'
    if source and source.checksum == digest:
        return 'unchanged'
    urls=[source_url]
    if key=='file:src/doc/index.rst' and (repository.mode=='workitem' or repository.document_type in {'tida-template','charter'}):
        urls.extend([repository.url,repository.url+'/',f'https://{repository.account.organization.lower()}.github.io/{repository.name}/'])
    doc = source.document if source else core.documents(include_uncatalogued=True).filter(external_url__in=urls).first()
    if dry_run:
        return 'update' if doc else 'create'
    if doc:
        doc = Document.objects.select_for_update().get(pk=doc.pk)
    if not source:
        source,created = GitHubSource.objects.get_or_create(repository=repository, source_key=key, defaults={'document':doc})
        if created and doc and doc.docevent_set.filter(by__user__isnull=False).exists():
            source.sync_mode='manual'
            source.source_url=source_url
            source.sha=sha
            source.save()
            return 'manual/excluded'
    source=GitHubSource.objects.select_for_update().get(pk=source.pk)
    if source.sync_mode!='automatic':
        return 'manual/excluded'
    if source.checksum==digest:
        return 'unchanged'
    if source.document_id and not doc:
        doc=Document.objects.select_for_update().get(pk=source.document_id)
    if doc and repository.mode=='workitem':
        # Main-branch source changes never manufacture an archived revision.
        # The authenticated PDF callback updates the working copy; tags archive it.
        source.sha,source.checksum,source.last_sync=sha,digest,timezone.now()
        source.save(update_fields=['sha','checksum','last_sync'])
        return 'working source changed; awaiting build'
    if not doc:
        name = 'fg-tida-'+slugify(repository.full_name+'-'+key)[:175]+'-'+hashlib.sha256((repository.full_name+':'+key).encode()).hexdigest()[:10]
        doc = Document(name=name, group=repository.group, type_id=kind or repository.document_type)
    doc.title = title
    doc.group=repository.group
    doc.abstract = data.decode('utf-8-sig')[:2000] if PurePosixPath(filename).suffix.lower() in {'.md','.txt'} else ''
    doc.external_url = source_url
    initial = 'draft' if repository.mode == 'workitem' or doc.type_id=='charter' else 'submitted'
    state = None if doc.pk and doc.states.exists() else State.objects.filter(type_id=doc.type_id, slug=initial if doc.type_id in {'tida','charter'} else 'active').first()
    core.save_document(doc, core.actor_for(), f'Synchronized {repository.full_name}: {key} ({sha}).', data=data, filename=filename, state=state)
    selected_category = category or repository.category
    if selected_category in dict(core.CATEGORIES):
        doc.tags.remove(*doc.tags.filter(slug__in=['tida-'+key for key,_ in core.CATEGORIES]))
        doc.tags.add('tida-'+selected_category)
    if doc.type_id == 'tida-template':
        doc.tags.add('tida-templates')
    # Update the native snapshot after categories have been attached.
    from ietf.doc.utils import save_document_in_history
    save_document_in_history(doc)
    source.document, source.sha, source.checksum = doc, sha, digest
    source.source_url, source.last_sync = source_url, timezone.now()
    source.save()
    if repository.mode == 'workitem':
        WorkItem.objects.get_or_create(document=doc, defaults={'repository':repository, 'short_name':slugify(title)[:48] or repository.name[:48]})
    if repository.document_type=='charter' and not repository.group.charter_id:
        repository.group.charter=doc
        repository.group.save(update_fields=['charter'])
    return 'updated' if doc.rev != '00' else 'created'


def sync_repository(repository, dry_run=False, client=None, metadata=None):
    client = client or GitHubClient(repository.account)
    results = []
    if not repository.enabled or not repository.account.enabled:
        return ['disabled']
    # Enforce the public-source boundary for webhook jobs as well as CLI imports.
    from django.core.exceptions import ValidationError
    metadata=metadata if metadata is not None else client.request('GET','/repos/'+repository.full_name)
    if not metadata or metadata.get('private') or metadata.get('visibility','public')!='public':
        raise ValidationError('Only public repositories may feed the public tracker catalog.')
    if repository.github_id and metadata.get('id')!=repository.github_id:
        raise ValidationError('Repository identity changed; administrator review is required.')
    if not dry_run and not repository.github_id:
        repository.github_id=metadata['id']
        repository.save(update_fields=['github_id'])
    if repository.mode == 'issues':
        for issue in client.pages(f'/repos/{repository.full_name}/issues?state=all'):
            if 'pull_request' in issue:
                continue
            body = ('# '+issue['title']+'\n\n'+(issue.get('body') or '')+'\n').encode()
            results.append(store_source(repository, 'issue:'+str(issue['number']), body, f'issue-{issue["number"]}.md',
                issue['title'][:255], issue['html_url'], issue['updated_at'], dry_run=dry_run))
            # Only explicitly linked GitHub-hosted document attachments are catalogued.
            for url in sorted(set(re.findall(r'https://github\.com/(?:user-attachments/files|[^\s/]+/[^\s/]+/files)/[^\s)<>\"\\]+',issue.get('body') or ''))):
                path=urlsplit(url).path
                filename=PurePosixPath(unquote(path)).name
                if PurePosixPath(filename).suffix.lower() not in SUFFIXES:
                    continue
                data=client.attachment(url)
                key='attachment:'+hashlib.sha256(url.encode()).hexdigest()
                results.append(store_source(repository,key,data,filename,issue['title'][:200]+' — '+filename[:50],
                    url,issue['updated_at'],dry_run=dry_run))
                if not dry_run:
                    from ietf.doc.models import RelatedDocument
                    from ietf.name.models import DocRelationshipName
                    parent=GitHubSource.objects.filter(repository=repository,source_key='issue:'+str(issue['number']),document__isnull=False).first()
                    attachment=GitHubSource.objects.filter(repository=repository,source_key=key,document__isnull=False).first()
                    if parent and attachment:
                        relation,_=DocRelationshipName.objects.get_or_create(slug='tida-material',defaults={'name':'Supporting material'})
                        RelatedDocument.objects.get_or_create(source=parent.document,target=attachment.document,relationship=relation)
        if not dry_run:
            repository.last_sync,repository.last_error=timezone.now(),''
            repository.save(update_fields=['last_sync','last_error'])
        return results
    commit = client.request('GET', f'/repos/{repository.full_name}/commits/{repository.branch}')
    if not commit:
        return ['repository unavailable']
    sha = commit['sha']
    tree = client.request('GET', f'/repos/{repository.full_name}/git/trees/{sha}?recursive=1')
    if not tree or tree.get('truncated'):
        raise ValueError('Repository tree is unavailable or truncated; narrow the document source.')
    files = [row for row in tree['tree'] if row['type']=='blob' and eligible(row['path'], repository.paths)]
    for row in files:
        path = row['path']
        if row.get('size',0) > 10*1024*1024:
            results.append('oversized file skipped')
            continue
        data = client.blob(repository, sha, path)
        # A Sphinx specification is one document, not one catalog entry per chapter.
        if path.endswith('/index.rst') and '/chapters/' not in path:
            base = str(PurePosixPath(path).parent)
            chapters = [part['path'] for part in tree['tree'] if part['type']=='blob' and part['path'].startswith(base+'/chapters/') and part['path'].endswith('.rst')]
            if len(chapters) > 80:
                raise ValueError('Too many specification chapters.')
            data += b'\n\n'+b'\n\n'.join(client.blob(repository,sha,part) for part in sorted(chapters))
            if len(data)>10*1024*1024:
                raise ValueError('Specification exceeds the document size limit.')
        kind='minutes' if PurePosixPath(path).name.lower()=='minutes.md' else 'agenda' if PurePosixPath(path).name.lower()=='agenda.md' else repository.document_type
        result=store_source(repository, 'file:'+path, data, PurePosixPath(path).name,
            title_for(data,path), repository.url+'/blob/'+repository.branch+'/'+path, row['sha'],kind=kind,dry_run=dry_run)
        results.append(result)
        if not dry_run and repository.name=='meetings':
            from ietf.meeting.models import Session,SessionPresentation
            meeting=core.meetings().filter(number='tida-'+PurePosixPath(path).parts[0]).first()
            if meeting:
                session=meeting.session_set.filter(group=repository.group).first()
                if not session:
                    session=Session.objects.create(meeting=meeting,group=repository.group,name='Meeting materials',type_id='session',purpose_id='regular')
                source=GitHubSource.objects.filter(repository=repository,source_key='file:'+path,document__isnull=False).first()
                if source and source.sync_mode=='automatic':
                    SessionPresentation.objects.get_or_create(session=session,document=source.document)
    if not dry_run:
        repository.last_sync, repository.last_error = timezone.now(), ''
        repository.save(update_fields=['last_sync','last_error'])
    return results


def preserve_manual_document(document):
    GitHubSource.objects.filter(document=document).update(sync_mode='manual')
    WorkItem.objects.filter(document=document).update(sync_mode='manual')


def preserve_deletion(sender, instance, **kwargs):
    GitHubSource.objects.filter(document=instance).update(sync_mode='excluded')
    WorkItem.objects.filter(document=instance).update(sync_mode='excluded')
