"""GitHub runner helper. Bearer tokens exist only in memory and are never logged."""
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


opener = urllib.request.build_opener(NoRedirect())


def inside(workspace, value):
    path=(workspace/value).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError('Artifact paths must stay inside the repository workspace.')
    return path


def main():
    workspace=Path(os.environ['GITHUB_WORKSPACE']).resolve()
    metadata=workspace/'.tracker/metadata.json'
    if not metadata.is_file():
        print('Tracker publishing skipped: merge the reviewed tracker metadata pull request first.')
        return
    document=json.loads(metadata.read_text(encoding='utf-8'))['document']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,220}',document):
        raise ValueError('Invalid tracker document identifier.')
    origin=os.environ['FGTIDA_TRACKER_ORIGIN'].rstrip('/')
    parsed=urllib.parse.urlsplit(origin)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Tracker URL must be an HTTPS origin.')
    pdf_path=inside(workspace,os.environ['FGTIDA_PDF'])
    if not pdf_path.is_file() or pdf_path.stat().st_size>10*1024*1024:
        raise ValueError('PDF is missing or exceeds 10 MB.')
    pdf=pdf_path.read_bytes()
    if not pdf.startswith(b'%PDF-'):
        raise ValueError('Build did not produce a PDF.')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=workspace,text=True).strip()
    if commit!=os.environ['GITHUB_SHA']:
        raise ValueError('Checked-out commit must match the Actions identity.')
    ref=os.environ['GITHUB_REF']
    tag=ref.removeprefix('refs/tags/') if ref.startswith('refs/tags/') else ''
    manifest={'schema':1,'repository':os.environ['GITHUB_REPOSITORY'],'document':document,
        'commit':commit,'pdf_sha256':hashlib.sha256(pdf).hexdigest()}
    archive=None
    if tag:
        if not re.fullmatch(r'v[0-9]+\.[0-9]+(?:\.[0-9]+)?',tag):
            raise ValueError('Unsupported release tag.')
        manifest['tag']=tag
        source=inside(workspace,os.environ['FGTIDA_SOURCE'])
        if not source.is_dir():
            raise ValueError('Source directory is missing.')
        stream=io.BytesIO()
        size=0
        files=0
        def normalized(info):
            info.uid=info.gid=0
            info.uname=info.gname=''
            info.mtime=0
            info.mode=0o644
            return info
        compressed=gzip.GzipFile(fileobj=stream,mode='wb',mtime=0,filename='')
        with compressed, tarfile.open(fileobj=compressed,mode='w') as target:
            for path in sorted(source.rglob('*')):
                if path.is_symlink():
                    raise ValueError('Source archive must not include symbolic links.')
                relative=path.relative_to(workspace)
                if any(part in {'.git','_build','__pycache__','settings_local.py','credentials.json','.npmrc','.pypirc','id_rsa','id_ed25519'} or part.startswith('.env') or part.endswith('.env') for part in relative.parts):
                    continue
                if path.is_file() and path.suffix.lower() not in {'.key','.pem','.p12','.pfx'}:
                    size+=path.stat().st_size
                    files+=1
                    if size>100*1024*1024 or files>5000:
                        raise ValueError('Source tree exceeds the archive limits.')
                    target.add(path,arcname=str(relative).replace('\\','/'),recursive=False,filter=normalized)
        archive=stream.getvalue()
        if len(archive)>25*1024*1024:
            raise ValueError('Compressed source archive exceeds 25 MB.')
        manifest['source_sha256']=hashlib.sha256(archive).hexdigest()
    endpoint=origin+'/integrations/github/build/'
    request_url=os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']+'&audience='+urllib.parse.quote(endpoint,safe='')
    identity_request=urllib.request.Request(request_url,headers={'Authorization':'bearer '+os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    with opener.open(identity_request,timeout=30) as response:
        token=json.load(response)['value']
    boundary='fgtida-'+uuid.uuid4().hex
    parts=[]
    def part(name,data,filename=None,mime='application/octet-stream'):
        header=f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if filename:
            header+=f'; filename="{filename}"'
        parts.append(header.encode()+f'\r\nContent-Type: {mime}\r\n\r\n'.encode()+data+b'\r\n')
    part('manifest',json.dumps(manifest).encode(),mime='application/json')
    part('pdf',pdf,'document.pdf','application/pdf')
    if archive is not None:
        part('source',archive,'source.tar.gz','application/gzip')
    body=b''.join(parts)+f'--{boundary}--\r\n'.encode()
    headers={'Authorization':'Bearer '+token,'X-FG-TIDA-Repository':manifest['repository'],
        'Content-Type':'multipart/form-data; boundary='+boundary}
    for attempt in range(4):
        request=urllib.request.Request(endpoint,data=body,headers=headers,method='POST')
        try:
            with opener.open(request,timeout=120) as response:
                result=json.load(response)
            print('Tracker accepted build:',result.get('result'),'revision',result.get('revision'))
            return
        except urllib.error.HTTPError as error:
            if error.code not in {429,502,503,504} or attempt==3:
                raise RuntimeError(f'Tracker publishing failed (HTTP {error.code}). Review tracker configuration and job history.') from None
            time.sleep(5*(attempt+1))


if __name__=='__main__':
    main()
