"""Reading/build packages for GitHub issues; the public source remains intact."""
import hashlib
import re
import subprocess
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

from .parser_process import environment
from .source_packages import pack, prepare_zip
from .source_versions import declared_version
from .submission_template import PROFILE, problems, sections
from .template_repairs import fit_text


def prepare_issue(original, *, title, credits, client=None):
    from .file_safety import validate_file
    validate_file(original, 'github-issue.md')
    if len(original) > 500_000:
        raise ValidationError('Issue text exceeds the 500 KB source limit.')
    try:
        result = subprocess.run(['pandoc', '--sandbox', '-f', 'gfm', '-t', 'rst', '--wrap=none'],
            input=original, capture_output=True, check=True, timeout=15, env=environment())
        text = result.stdout.decode('utf-8')
    except (subprocess.SubprocessError, OSError, UnicodeError) as error:
        raise ValidationError('Cannot prepare the GitHub issue reading package; the original remains unchanged.') from error
    # Only explicit document version fields count, including GitHub blockquotes.
    normalized = re.sub(r'(?m)^\s*>\s?', '', original.decode('utf-8-sig'))
    version = declared_version(normalized) or '0.0'
    # GitHub's newer use cases declare the case owner and co-authors in a
    # quoted identification header. Read only that explicit identity material.
    from .github_attribution import document_attribution, CREDIT_FIELDS
    identity = re.split(r'(?im)^#{1,6}\s+2[.)]\s+', normalized, maxsplit=1)[0]
    identity = identity.replace('**', '')
    identity = re.sub(r'(?im)^Case owner\s*:', 'Author:', identity)
    identity = re.sub(r'(?im)^(Contributors?\s*:\s*)([^\n]+)',
                      lambda match: match[1] + match[2].replace(';', ','), identity)
    observed_credits = document_attribution(identity)
    # The normalized declaration supersedes older parsers' unsplit values
    # (e.g. "First Author; Second Author" must not become a third person).
    credits = {field: observed_credits.get(field) or credits.get(field, []) for field in CREDIT_FIELDS}
    for name, affiliation in re.findall(r'(?m)^\s*-\s+([^\n:]+?)\s+—\s+([^\n:]+):', identity):
        if name.strip() in credits['authors'] and affiliation.strip() not in credits['affiliations']:
            credits['affiliations'].append(affiliation.strip())
    _, blocks = sections(text)
    abstract = next(('\n\n'.join(body) for heading, body in blocks.items()
        if heading.casefold() in {'abstract', 'summary', 'short summary', 'plain-language description'}), '')
    if not abstract:
        field = re.search(r'(?im)^\*\*(?:Plain[- ]language description|Short summary|Summary|Abstract)'
                          r'(?::\*\*|\*\*(?:\s+\*[^*\n]*\*)?\s*:)\s*(.+)', text)
        abstract = field[1].strip() if field else ''
    adapted = fit_text(text, title=title, version=version,
        authors=credits.get('authors') or (), affiliations=credits.get('affiliations') or (),
        contacts=credits.get('contact_emails') or (), abstract=abstract,
        working_group='wg0: Use Cases, Taxonomy, and Terminology (UCT)')
    files = {'original/github-issue.md': original}
    # Embed only explicitly referenced, bounded GitHub-hosted images. The client
    # checks each redirect against its existing public asset-host allowlist.
    for match in list(re.finditer(r'(?m)^(\s*\.\.\s+(?:image|figure)::\s*)(\S+)', adapted))[::-1]:
        url = match[2]
        extension = PurePosixPath(urlsplit(url).path).suffix.lower()
        if not client or extension not in {'.png', '.jpg', '.jpeg', '.gif', '.svg'}:
            raise ValidationError('An issue image needs a supported public GitHub image URL; no image was silently omitted.')
        filename = 'images/' + hashlib.sha256(url.encode()).hexdigest()[:20] + extension
        files[filename] = client.attachment(url)
        adapted = adapted[:match.start(2)] + filename + adapted[match.end(2):]
    files['index.rst'] = adapted.encode('utf-8')
    archive, manifest = prepare_zip(pack(files), 'index.rst', complete=True)
    issues = problems(adapted)
    # Do not invent missing author content just to pass a completeness check.
    # The original is retained inside the package and linked on the document.
    metadata = {**credits, 'snapshot': manifest, 'template_adaptation': {
        'profile': PROFILE, 'provisional': bool(issues), 'author_review_pending': bool(issues),
        'issues': issues, 'original_sha256': hashlib.sha256(original).hexdigest(),
        'origin': 'GitHub issue; template reading adaptation', 'version': version}}
    return archive, metadata
