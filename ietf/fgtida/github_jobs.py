"""Durable, retryable GitHub side effects; no credentials or mail in repositories."""
import ast
import base64
import json
import hashlib
from datetime import timedelta
from urllib.parse import quote
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from .github_client import GitHubClient
from .models import GitHubJob, GitHubRepository
from .work_items import state_of

LABEL_COLORS = {'draft':'6c757d','review':'ffc107','published':'198754','withdrawn':'dc3545'}


def write_client(item):
    repository = item.repository
    config = getattr(item.document.group, 'tida_configuration', None)
    if (not repository.enabled or not repository.account.enabled or repository.mode != 'workitem'
            or repository.group_id != item.document.group_id or not config
            or config.github_account_id != repository.account_id):
        raise ValidationError('Select an enabled versioned repository in the work item’s configured working group organization.')
    client = GitHubClient(repository.account, write=True)
    remote = client.request('GET', '/repos/'+repository.full_name)
    if (not remote or not repository.github_id or remote['id'] != repository.github_id
            or remote['full_name'].casefold() != repository.full_name.casefold()
            or remote.get('private') or remote.get('archived') or remote.get('disabled')):
        raise ValidationError('Verify the current public GitHub repository name and ID before writing.')
    return client


def update_state(item, client):
    if not item.document_id or not item.repository_id:
        return
    state = state_of(item.document)
    if state not in LABEL_COLORS:
        return
    repo = item.repository.full_name
    name = 'tida-state: '+state
    description = 'Managed by FG-TIDA Datatracker. Current lifecycle state: '+state+'.'
    labels = list(client.pages('/repos/'+repo+'/labels'))
    managed = next((label for label in labels if (label.get('description') or '').startswith('Managed by FG-TIDA Datatracker.')), None)
    data = {'name':name,'color':LABEL_COLORS[state], 'description':description}
    if managed:
        client.request('PATCH','/repos/'+repo+'/labels/'+quote(managed['name'],safe=''),data)
    elif any(label['name']==name for label in labels):
        raise ValidationError('The desired lifecycle label already exists without the tracker ownership marker.')
    else:
        client.request('POST','/repos/'+repo+'/labels',data)


def bootstrap_repository(item, client):
    document = item.document
    repo = item.repository.full_name
    from .models import Contribution
    contribution=Contribution.objects.filter(document=document).first()
    version = document.tida_versions.filter(revision=document.rev).first() if contribution else None
    native_authors = list(document.documentauthor_set.order_by('order').values_list('person__name', 'affiliation', 'person__user_id'))
    authors = [{'name': name, 'affiliation': affiliation} for name, affiliation, _ in native_authors]
    override = getattr(document, 'tida_attribution', None)
    if override and override.authors:
        authors = [{'name': name.strip()} for name in override.authors.splitlines() if name.strip()]
    elif not override and version and (not authors or (len(authors) == 1 and native_authors[0][2] == contribution.submitter_id)):
        authors = [{'name': name} for name in version.source_metadata.get('authors', [])] or authors
    from .accounts import mail_origin
    origin = mail_origin(None).rstrip('/')
    metadata = {'document':document.name,'title':document.title,'authors':authors,
        'tracker_url':origin+'/doc/'+document.name+'/','group':document.group.name}
    draft=contribution.draft_repository_url if contribution else ''
    if draft:
        metadata['original_draft_repository']=draft
    source_archive = bytes(contribution.source_archive) if contribution and contribution.source_archive else b''
    source_manifest = contribution.source_manifest if contribution else {}
    if version and version.source_metadata.get('snapshot'):
        from pathlib import Path
        source_manifest = version.source_metadata['snapshot']
        try:
            source_archive = Path(document.get_file_name()).read_bytes()
        except OSError as error:
            raise ValidationError('The current contribution revision archive is unavailable.') from error
        if hashlib.sha256(source_archive).hexdigest() != source_manifest['sha256']:
            raise ValidationError('The current contribution revision archive failed its checksum.')
    if source_archive:
        from .file_safety import validate_file
        validate_file(source_archive, 'source.zip')
        if (source_manifest.get('method', getattr(contribution, 'submission_method', '')) == 'word'
                and not getattr(contribution, 'conversion_verified_at', None)):
            raise ValidationError('Word conversion must be verified before source import.')
        metadata['submitted_source'] = source_manifest
    serialized=json.dumps(metadata,ensure_ascii=False,indent=2)+'\n'
    main_metadata=client.request('GET','/repos/'+repo+'/contents/.tracker/metadata.json?ref='+quote(item.repository.branch,safe=''))
    if main_metadata:
        published_metadata = base64.b64decode(main_metadata.get('content', ''))
        existing = json.loads(published_metadata)
        if not isinstance(existing, dict) or existing.get('document') != document.name:
            raise ValidationError('GitHub metadata already links this repository to a different tracker document.')
        if published_metadata == serialized.encode():
            update_state(item,client)
            return
    branch='tracker/metadata-'+str(item.pk)+'-'+hashlib.sha256(serialized.encode()).hexdigest()[:12]
    ref = client.request('GET','/repos/'+repo+'/git/ref/heads/'+item.repository.branch)
    if not ref:
        raise ValidationError('The repository default branch is not ready; retry provisioning shortly.')
    if not client.request('GET','/repos/'+repo+'/git/ref/heads/'+quote(branch,safe='')):
        client.request('POST','/repos/'+repo+'/git/refs',{'ref':'refs/heads/'+branch,'sha':ref['object']['sha']})
    files = {'.tracker/metadata.json':serialized}
    initial_files = ['src/doc/conf.py', 'src/doc/index.rst'] if not main_metadata else []
    for path in [*initial_files, 'README.md']:
        response = client.request('GET','/repos/'+repo+'/contents/'+path+'?ref='+quote(branch,safe=''))
        if not response:
            continue
        text = base64.b64decode(response['content']).decode('utf-8')
        if path.endswith('conf.py'):
            lines = text.splitlines(keepends=True)
            replacements = {'project':document.title,'author':', '.join(author['name'] for author in authors)}
            for node in reversed(ast.parse(text).body):
                if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in replacements:
                    lines[node.lineno-1:node.end_lineno] = [node.targets[0].id+' = '+repr(replacements[node.targets[0].id])+'\n']
            text = ''.join(lines)
        elif path.endswith('index.rst'):
            lines = text.splitlines()
            for index in range(len(lines)-1):
                if lines[index].strip() and len(set(lines[index+1].strip()))==1 and lines[index+1].strip()[0:1] in {'=','-','~'}:
                    title = document.title.replace('\r',' ').replace('\n',' ').replace('..','\\.\\.')
                    lines[index:index+2] = [title, '='*max(3,len(title))]
                    break
            text = '\n'.join(lines)+'\n'
        else:
            marker = '<!-- FG-TIDA tracker -->'
            text = text.split(marker)[0].rstrip()+'\n\n'+marker+'\n'+f'[![Tracker state]({origin}/work-items/{item.pk}/state.svg)]({metadata["tracker_url"]})\n'
        files[path] = text
    if source_archive and not main_metadata:
        from .source_packages import unpack
        from .word_scaffold import PROFILE, scaffold
        template_files = scaffold()[1]
        # Import document content on the review branch, preserving the official
        # template's executable configuration and GitHub workflows.
        for name, content in unpack(source_archive).items():
            if name == 'conf.py' or name.endswith('.py'):
                continue
            if template_files.get(name) == content or (source_manifest.get('package_profile') == PROFILE
                    and name in {'requirements.txt', 'LICENSE.txt', 'README.md', 'conversion.json'}):
                continue  # Keep the official repository's build scaffold and licensing.
            files['src/doc/' + name] = content
        entry = source_manifest['entry_document']
        if entry != 'index.rst':
            files['src/doc/index.rst'] = '.. include:: ' + entry + '\n'
    for path, text in files.items():
        api_path = '/repos/'+repo+'/contents/'+quote(path, safe='/')
        current = client.request('GET',api_path+'?ref='+quote(branch,safe=''))
        content = text if isinstance(text, bytes) else text.encode()
        if current and base64.b64decode(current.get('content','')) == content:
            continue
        data = {'message':'Connect FG-TIDA tracker metadata','branch':branch,'content':base64.b64encode(content).decode()}
        if current:
            data['sha']=current['sha']
        client.request('PUT',api_path,data)
    pulls = client.request('GET','/repos/'+repo+'/pulls?state=open&head='+quote(item.repository.account.organization+':'+branch,safe='')) or []
    if not pulls:
        client.request('POST','/repos/'+repo+'/pulls',{'title':'Connect FG-TIDA Datatracker work item',
            'head':branch,'base':item.repository.branch,'body':'Review the imported source, assets and metadata before merging. Submitted executable configuration is not imported; the official template configuration is retained. '
            'The tracker does not push directly to main. Install the approved tracker-sync workflow in the canonical templates to enable build publishing.'})
    update_state(item,client)


@transaction.atomic
def provision(item):
    if not item.document_id or item.sync_mode != 'automatic' or state_of(item.document) not in {'draft','review'}:
        return
    if not item.repository_id:
        config = getattr(item.document.group, 'tida_configuration', None)
        if not config or not config.github_account_id or not config.github_account.enabled:
            raise ValidationError('Configure this WG’s GitHub organization and App installation first.')
        client = GitHubClient(config.github_account,write=True)
        name = f'wg{config.number}-{config.topic_acronym}-'+('charter' if item.document.type_id=='charter' else item.short_name)
        marker = 'FG-TIDA tracker work item '+item.document.name
        repo = client.request('GET',f'/repos/{config.github_account.organization}/{name}')
        if repo and repo.get('description') != marker:
            raise ValidationError('A repository with this name already exists. Link it explicitly after administrator review.')
        if not repo:
            template = 'wg-charter-template' if item.document.type_id=='charter' else 'spec-template'
            repo = client.request('POST',f'/repos/FG-TIDA/{template}/generate',{
                'owner':config.github_account.organization,'name':name,'description':marker,'private':False,'include_all_branches':False})
        repository, _ = GitHubRepository.objects.get_or_create(account=config.github_account,name=name,
            defaults={'group':item.document.group,'github_id':repo['id'], 'group_evidence':'https://github.com/'+config.github_account.organization+'/'+name})
        item.repository=repository
        item.save(update_fields=['repository'])
    client = write_client(item)
    bootstrap_repository(item,client)


def run_jobs(limit=10):
    completed=0
    for _ in range(limit):
        with transaction.atomic():
            jobs=GitHubJob.objects.select_for_update(skip_locked=True).filter(completed__isnull=True,attempts__lt=10,available_at__lte=timezone.now()).order_by('created')
            if not getattr(settings,'FGTIDA_GITHUB_WRITE_ENABLED',False):
                jobs=jobs.filter(kind='catalog')
            job=jobs.first()
            if not job:
                break
            job.attempts+=1
            try:
                with transaction.atomic():
                    if job.kind=='catalog':
                        from .github_catalog import sync_repository
                        sync_repository(job.repository)
                    elif job.kind=='provision':
                        provision(job.work_item)
                    elif job.kind == 'state' and job.work_item.repository_id and job.work_item.document_id and job.work_item.sync_mode == 'automatic':
                        update_state(job.work_item, write_client(job.work_item))
            except Exception as error:
                job.last_error=f'{type(error).__name__}: operation failed; review configuration or permissions.'
                job.available_at=timezone.now()+timedelta(minutes=min(2**job.attempts,60))
            else:
                job.completed=timezone.now()
                job.last_error=''
                completed+=1
            job.save(update_fields=['attempts','completed','last_error','available_at'])
    return completed
