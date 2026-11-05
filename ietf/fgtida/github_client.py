"""Bounded GitHub API client. App credentials stay in server secret files."""
import base64
import json
from pathlib import Path, PurePosixPath
import re
import time
from urllib.parse import quote, urlsplit
import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.views.decorators.debug import sensitive_variables

API = 'https://api.github.com'


def repository_name(url):
    parsed = urlsplit(url or '')
    parts = parsed.path.strip('/').split('/')
    if (parsed.scheme != 'https' or parsed.netloc.lower() != 'github.com' or parsed.query or parsed.fragment
            or len(parts) != 2 or not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', part) and part not in {'.', '..'} for part in parts)):
        raise ValidationError('Use an HTTPS GitHub repository URL, without credentials, query strings or subpaths.')
    return '/'.join(parts).removesuffix('.git')


@sensitive_variables()
def installation_token(account):
    app_id = getattr(settings, 'FGTIDA_GITHUB_APP_ID', '')
    key_path = getattr(settings, 'FGTIDA_GITHUB_PRIVATE_KEY_FILE', '')
    if not app_id or not key_path or not account.installation_id:
        return None
    from jwcrypto import jwk, jwt
    now = int(time.time())
    key = jwk.JWK.from_pem(Path(key_path).read_bytes())
    token = jwt.JWT(header={'alg': 'RS256'}, claims={'iat':now-30, 'exp':now+300, 'iss':str(app_id)})
    token.make_signed_token(key)
    response = requests.post(f'{API}/app/installations/{account.installation_id}/access_tokens',
        headers={'Authorization':'Bearer '+token.serialize(), 'Accept':'application/vnd.github+json'}, timeout=(5,20), allow_redirects=False)
    if response.status_code != 201:
        raise ValidationError('GitHub App authentication failed. Check installation permissions and server secret configuration.')
    return response.json()['token']


class GitHubClient:
    @sensitive_variables()
    def __init__(self, account, write=False):
        self.account = account
        self.token = installation_token(account)
        self.write = write
        if write and (not getattr(settings, 'FGTIDA_GITHUB_WRITE_ENABLED', False) or not self.token):
            raise ValidationError('GitHub writes are disabled or the GitHub App is not configured.')

    @sensitive_variables()
    def request(self, method, path, data=None, limit=8*1024*1024):
        if not path.startswith('/') or path.startswith('//') or '..' in PurePosixPath(path.split('?')[0]).parts:
            raise ValidationError('Invalid GitHub API path.')
        if method != 'GET' and not self.write:
            raise ValidationError('GitHub write was not enabled for this operation.')
        headers = {'Accept':'application/vnd.github+json', 'User-Agent':'FG-TIDA-Datatracker'}
        if self.token:
            headers['Authorization'] = 'Bearer '+self.token
        with requests.request(method, API+path, json=data, headers=headers, timeout=(5,30), allow_redirects=False, stream=True) as response:
            if response.status_code == 404:
                return None
            if response.status_code not in {200,201,202,204}:
                raise ValidationError(f'GitHub API returned HTTP {response.status_code}. Check permissions or rate limits.')
            result = bytearray()
            for chunk in response.iter_content(65536):
                result.extend(chunk)
                if len(result) > limit:
                    raise ValidationError('GitHub API response exceeded the configured size limit.')
            return json.loads(result) if result else {}

    def pages(self, path):
        for page in range(1, 21):
            result = self.request('GET', path+('&' if '?' in path else '?')+f'per_page=100&page={page}') or []
            yield from result
            if len(result) < 100:
                return
        raise ValidationError('Too many GitHub records; narrow the repository configuration.')

    def blob(self, repository, commit, path, limit=10*1024*1024):
        if not re.fullmatch(r'[0-9a-f]{40}', commit) or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
            raise ValidationError('Invalid GitHub source path or commit.')
        # Public catalog downloads do not transmit the App token to another host.
        url = 'https://raw.githubusercontent.com/'+repository.full_name+'/'+commit+'/'+quote(path, safe='/')
        with requests.get(url, headers={'User-Agent':'FG-TIDA-Datatracker'}, timeout=(5,30), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValidationError('Cannot download the public GitHub document.')
            result = bytearray()
            for chunk in response.iter_content(65536):
                result.extend(chunk)
                if len(result) > limit:
                    raise ValidationError('GitHub document exceeded 10 MB.')
            return bytes(result)

    def attachment(self, url):
        """Download public GitHub issue attachments without forwarding any credentials."""
        allowed = {'github.com','github-production-user-asset-6210df.s3.amazonaws.com',
                   'github-production-user-asset-6210df.s3.us-east-1.amazonaws.com',
                   'objects.githubusercontent.com','private-user-images.githubusercontent.com'}
        for hop in range(4):
            parsed=urlsplit(url)
            if (parsed.scheme!='https' or parsed.hostname not in allowed or parsed.username
                    or parsed.password or parsed.port not in {None,443}):
                raise ValidationError('Attachment redirects must stay on approved GitHub asset hosts.')
            with requests.get(url,timeout=(5,30),allow_redirects=False,stream=True) as response:
                if response.status_code in {301,302,303,307,308}:
                    from urllib.parse import urljoin
                    url=urljoin(url,response.headers.get('Location',''))
                    continue
                if response.status_code!=200:
                    raise ValidationError('Public GitHub attachment is unavailable.')
                result=bytearray()
                for chunk in response.iter_content(65536):
                    result.extend(chunk)
                    if len(result)>10*1024*1024:
                        raise ValidationError('GitHub attachment exceeds 10 MB.')
                return bytes(result)
        raise ValidationError('Too many attachment redirects.')


@sensitive_variables()
def verify_build_identity(token, repository, manifest=None):
    """Trust signed Actions identities only for this explicitly registered repo."""
    if not token or len(token) > 20000 or not repository.github_id:
        raise ValidationError('A configured repository and GitHub Actions identity are required.')
    from jwcrypto import jwk, jwt
    from django.core.cache import cache
    keys = cache.get('fgtida:github:oidc-keys')
    if not keys:
        response = requests.get('https://token.actions.githubusercontent.com/.well-known/jwks', timeout=(5,15), allow_redirects=False)
        if response.status_code != 200 or len(response.content) > 100000:
            raise ValidationError('GitHub identity verification is temporarily unavailable.')
        keys = response.text
        cache.set('fgtida:github:oidc-keys', keys, 300)
    audience = settings.SITE_URL.rstrip('/')+'/integrations/github/build/'
    try:
        claims = json.loads(jwt.JWT(jwt=token, key=jwk.JWKSet.from_json(keys), algs=['RS256'],
            check_claims={'iss':'https://token.actions.githubusercontent.com', 'aud':audience, 'exp':None, 'nbf':None}).claims)
    except Exception as error:
        raise ValidationError('Invalid or expired GitHub Actions identity.') from error
    ref = claims.get('ref', '')
    now=int(time.time())
    if (not isinstance(claims.get('exp'),int) or claims['exp']<=now or claims['exp']>now+900
            or not isinstance(claims.get('iat'),int) or claims['iat']<now-900 or claims['iat']>now+30):
        raise ValidationError('Invalid GitHub identity lifetime.')
    expected = repository.full_name+'/'+repository.publish_workflow+'@'+ref
    if (claims.get('repository', '').casefold() != repository.full_name.casefold()
            or str(claims.get('repository_id')) != str(repository.github_id)
            or claims.get('event_name') not in {'push','workflow_dispatch'}
            or claims.get('workflow_ref', '').casefold() != expected.casefold()):
        raise ValidationError('GitHub identity does not match the registered repository, workflow or commit.')
    tag = ref.removeprefix('refs/tags/') if ref.startswith('refs/tags/') else ''
    if manifest and (claims.get('sha')!=manifest.get('commit') or tag!=manifest.get('tag','')):
        raise ValidationError('Manifest does not match the signed GitHub identity.')
    if ref != ('refs/tags/'+tag if tag else 'refs/heads/'+repository.branch):
        raise ValidationError('Only the configured main branch or a release tag can publish a build.')
    client = GitHubClient(repository.account)
    if tag:
        from .work_items import version_tuple
        version_tuple(tag)
        result = client.request('GET', f'/repos/{repository.full_name}/git/ref/tags/{quote(tag,safe="")}')
        obj = result.get('object', {}) if result else {}
        for _ in range(4):
            if obj.get('type') != 'tag':
                break
            result = client.request('GET', f'/repos/{repository.full_name}/git/tags/{obj["sha"]}')
            obj = result.get('object', {}) if result else {}
        expected_sha = obj.get('sha') if obj.get('type') == 'commit' else None
    else:
        result = client.request('GET', f'/repos/{repository.full_name}/commits/{quote(repository.branch,safe="")}')
        expected_sha = result.get('sha') if result else None
    if expected_sha != claims.get('sha'):
        raise ValidationError('The build is stale or does not match the current branch/tag.')
    return claims
