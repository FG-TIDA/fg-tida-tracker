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
    authors = list(document.documentauthor_set.order_by('order').values_list('person__name',flat=True))
    origin = settings.SITE_URL.rstrip('/')
    metadata = {'document':document.name,'title':document.title,'authors':authors,
        'tracker_url':origin+'/doc/'+document.name+'/','group':document.group.name}
    from .models import Contribution
    draft=Contribution.objects.filter(document=document).values_list('draft_repository_url',flat=True).first()
    if draft:
        metadata['original_draft_repository']=draft
    serialized=json.dumps(metadata,ensure_ascii=False,indent=2)+'\n'
    main_metadata=client.request('GET','/repos/'+repo+'/contents/.tracker/metadata.json?ref='+quote(item.repository.branch,safe=''))
    if main_metadata and base64.b64decode(main_metadata.get('content',''))==serialized.encode():
        update_state(item,client)
        return
    branch='tracker/metadata-'+str(item.pk)+'-'+hashlib.sha256(serialized.encode()).hexdigest()[:12]
    ref = client.request('GET','/repos/'+repo+'/git/ref/heads/'+item.repository.branch)
    if not ref:
        raise ValidationError('The repository default branch is not ready; retry provisioning shortly.')
    if not client.request('GET','/repos/'+repo+'/git/ref/heads/'+quote(branch,safe='')):
        client.request('POST','/repos/'+repo+'/git/refs',{'ref':'refs/heads/'+branch,'sha':ref['object']['sha']})
    files = {'.tracker/metadata.json':serialized}
    for path in ['src/doc/conf.py','src/doc/index.rst','README.md']:
        response = client.request('GET','/repos/'+repo+'/contents/'+path+'?ref='+quote(branch,safe=''))
        if not response:
            continue
        text = base64.b64decode(response['content']).decode('utf-8')
        if path.endswith('conf.py'):
            lines = text.splitlines(keepends=True)
            replacements = {'project':document.title,'author':', '.join(authors)}
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
    for path, text in files.items():
        current = client.request('GET','/repos/'+repo+'/contents/'+path+'?ref='+quote(branch,safe=''))
        content = text.encode()
        if current and base64.b64decode(current.get('content','')) == content:
            continue
        data = {'message':'Connect FG-TIDA tracker metadata','branch':branch,'content':base64.b64encode(content).decode()}
        if current:
            data['sha']=current['sha']
        client.request('PUT','/repos/'+repo+'/contents/'+path,data)
    pulls = client.request('GET','/repos/'+repo+'/pulls?state=open&head='+quote(item.repository.account.organization+':'+branch,safe='')) or []
    if not pulls:
        client.request('POST','/repos/'+repo+'/pulls',{'title':'Connect FG-TIDA Datatracker work item',
            'head':branch,'base':item.repository.branch,'body':'Review this metadata and the submitted document before merging. '
            'The tracker does not push directly to main. Install the approved tracker-sync workflow in the canonical templates to enable build publishing.'})
    update_state(item,client)


@transaction.atomic
def provision(item):
    if not item.document_id or state_of(item.document) not in {'draft','review'}:
        return
    if not item.repository_id:
        config = item.document.group.tida_configuration
        if not config.github_account_id:
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
            defaults={'group':item.document.group,'github_id':repo['id']})
        item.repository=repository
        item.save(update_fields=['repository'])
    client = GitHubClient(item.repository.account,write=True)
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
                    elif job.kind=='state' and job.work_item.repository_id:
                        update_state(job.work_item,GitHubClient(job.work_item.repository.account,write=True))
            except Exception as error:
                job.last_error=f'{type(error).__name__}: operation failed; review configuration or permissions.'
                job.available_at=timezone.now()+timedelta(minutes=min(2**job.attempts,60))
            else:
                job.completed=timezone.now()
                job.last_error=''
                completed+=1
            job.save(update_fields=['attempts','completed','last_error','available_at'])
    return completed
