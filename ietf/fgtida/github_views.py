import hashlib
import hmac
import io
import json
import uuid
import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse, HttpResponse, FileResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe
from django.views.decorators.debug import sensitive_variables, sensitive_post_parameters
from . import core
from .models import GitHubRepository, GitHubDelivery, GitHubJob, WorkItem, WorkItemRelease
from .github_client import verify_build_identity
from .work_items import receive_build, state_of


@csrf_exempt
@require_POST
@sensitive_variables()
@sensitive_post_parameters()
def build(request):
    try:
        if not getattr(settings,'FGTIDA_GITHUB_INGEST_ENABLED',False):
            return JsonResponse({'error':'GitHub build publishing is not enabled.'},status=503)
        authorization=request.headers.get('Authorization','')
        if not authorization.startswith('Bearer '):
            return JsonResponse({'error':'GitHub Actions identity required.'},status=401)
        owner,name=request.headers.get('X-FG-TIDA-Repository','').split('/',1)
        repository=get_object_or_404(GitHubRepository.objects.select_related('account'),account__organization__iexact=owner,name=name,enabled=True,account__enabled=True,mode='workitem')
        # Authenticate before Django parses/spools multipart files.
        claims=verify_build_identity(authorization[7:],repository)
        raw=request.POST.get('manifest','')
        if len(raw)>65536:
            raise ValidationError('Manifest is too large.')
        manifest=json.loads(raw)
        if not isinstance(manifest,dict) or set(manifest)-{'schema','repository','document','commit','tag','pdf_sha256','source_sha256'}:
            raise ValidationError('Invalid manifest schema.')
        if manifest.get('schema') != 1 or not isinstance(manifest.get('repository'),str):
            raise ValidationError('Unsupported manifest schema.')
        tag=claims['ref'].removeprefix('refs/tags/') if claims['ref'].startswith('refs/tags/') else ''
        if (manifest['repository'].casefold()!=repository.full_name.casefold()
                or manifest.get('commit')!=claims['sha'] or manifest.get('tag','')!=tag):
            raise ValidationError('Manifest does not match the authenticated build.')
        if repository.work_item.document_id is None or manifest.get('document')!=repository.work_item.document.name:
            raise ValidationError('Build does not identify the linked tracker work item.')
        upload=request.FILES.get('pdf')
        if not upload or upload.size>10*1024*1024:
            raise ValidationError('PDF is missing or exceeds 10 MB.')
        pdf=upload.read(10*1024*1024+1)
        archive=request.FILES.get('source')
        if archive and archive.size>25*1024*1024:
            raise ValidationError('Source archive exceeds 25 MB.')
        data=archive.read(25*1024*1024+1) if archive else None
        result,revision=receive_build(repository,manifest,pdf,data)
        return JsonResponse({'result':result,'revision':revision})
    except (ValidationError,ValueError,KeyError,TypeError,WorkItem.DoesNotExist) as error:
        # Do not log/return the bearer token, signed claims or upload contents.
        message='; '.join(error.messages) if isinstance(error,ValidationError) else 'Invalid build request.'
        return JsonResponse({'error':message},status=409 if isinstance(error,ValidationError) else 400)
    except (requests.RequestException, OSError):
        return JsonResponse({'error':'GitHub identity verification is temporarily unavailable. Retry shortly.'},status=503)


@csrf_exempt
@require_POST
def webhook(request):
    secret=getattr(settings,'FGTIDA_GITHUB_WEBHOOK_SECRET','')
    if not secret:
        return JsonResponse({'error':'GitHub webhooks are not configured.'},status=503)
    try:
        length=int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        return HttpResponse(status=400)
    if length>1024*1024:
        return HttpResponse(status=413)
    body=request.body
    if len(body)>1024*1024:
        return HttpResponse(status=413)
    expected='sha256='+hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,request.headers.get('X-Hub-Signature-256','')):
        return HttpResponse(status=401)
    try:
        delivery=uuid.UUID(request.headers.get('X-GitHub-Delivery',''))
        payload=json.loads(body)
        event=request.headers.get('X-GitHub-Event','')
        if event=='ping':
            return JsonResponse({'result':'ready'})
        identity=payload.get('repository',{})
        repository=GitHubRepository.objects.filter(github_id=identity.get('id'),enabled=True,account__enabled=True).first()
        if not repository or identity.get('full_name','').casefold()!=repository.full_name.casefold():
            return HttpResponse(status=403)
        with transaction.atomic():
            record,created=GitHubDelivery.objects.get_or_create(delivery_id=delivery,defaults={'repository':repository,'event':event[:32]})
            if not created:
                return JsonResponse({'result':'already received'})
            if repository.mode!='workitem' and event in {'push','issues'}:
                if event=='push' and payload.get('ref')!='refs/heads/'+repository.branch:
                    return JsonResponse({'result':'ignored branch'})
                GitHubJob.objects.get_or_create(kind='catalog',repository=repository,key='delivery:'+str(delivery))
        return JsonResponse({'result':'queued; builds publish through authenticated Actions'})
    except (ValueError,TypeError,AttributeError):
        return HttpResponse(status=400)


def public_item(pk):
    return get_object_or_404(WorkItem.objects.defer('working_pdf').select_related('document','repository'),pk=pk,document__in=core.documents())


@require_safe
def state_badge(request,pk):
    item=public_item(pk)
    state=state_of(item.document)
    from .github_jobs import LABEL_COLORS
    from xml.sax.saxutils import escape
    label={'review':'Under review'}.get(state,state.capitalize())
    color=LABEL_COLORS.get(state,'0d6efd')
    foreground='#000' if state=='review' else '#fff'
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="220" height="24" role="img" aria-label="Tracker state: {escape(label)}"><rect width="110" height="24" fill="#555"/><rect x="110" width="110" height="24" fill="#{color}"/><g fill="#fff" font-family="Arial,sans-serif" font-size="12" text-anchor="middle"><text x="55" y="16">FG-TIDA</text><text x="165" y="16" fill="{foreground}">{escape(label)}</text></g></svg>'
    return HttpResponse(svg,content_type='image/svg+xml',headers={'Cache-Control':'public, max-age=60','X-Content-Type-Options':'nosniff'})


@require_safe
def working_pdf(request,pk):
    item=public_item(pk)
    if not item.working_pdf:
        from django.http import Http404
        raise Http404
    return FileResponse(io.BytesIO(bytes(item.working_pdf)),content_type='application/pdf',filename=item.document.name+'-working.pdf',headers={'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'})


@require_safe
def release_artifact(request,pk,revision,kind):
    item=public_item(pk)
    release=get_object_or_404(WorkItemRelease,work_item=item,revision=revision)
    if kind=='manifest':
        data=json.dumps(release.manifest,ensure_ascii=False,indent=2).encode()
        mime,extension='application/json','.json'
    elif kind=='source':
        data=bytes(release.source_archive)
        mime,extension='application/gzip','.tar.gz'
    else:
        from django.http import Http404
        raise Http404
    return FileResponse(io.BytesIO(data),as_attachment=True,filename=item.document.name+'-'+revision+extension,content_type=mime,headers={'X-Content-Type-Options':'nosniff'})
