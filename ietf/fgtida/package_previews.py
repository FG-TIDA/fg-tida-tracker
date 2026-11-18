"""Preview scanned package images without extracting files to public storage."""
import posixpath
import re
from pathlib import PurePosixPath

from django.http import Http404, HttpResponse
from django.utils.http import content_disposition_header

from .source_packages import unpack

IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
               '.gif': 'image/gif', '.svg': 'image/svg+xml'}
IMAGE_DIRECTIVE = re.compile(r'(?m)^(\s*\.\.\s+(?:\|[^|\n]+\|\s+)?(?:image|figure)::\s*)([^\n]+?)\s*$')


def chapters(data, asset_url):
    """Resolve each image against its chapter, preserving nested source paths.

    Call only after the archive has passed file_safety checks. The URL builder
    identifies either the private contribution or a particular public revision.
    """
    files = unpack(data)
    result = []
    for name, content in sorted(files.items()):
        if not name.endswith('.rst'):
            continue
        image_urls = set()
        def replace(match):
            target = match[2].strip()
            path = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if (target.startswith('/') or ':' in target or '\\' in target
                    or path not in files or PurePosixPath(path).suffix.lower() not in IMAGE_TYPES):
                return match[0]
            url = asset_url(path)
            image_urls.add(url)
            return match[1] + url
        text = IMAGE_DIRECTIVE.sub(replace, content.decode('utf-8-sig'))
        result.append((name, text, {'image_urls': image_urls}))
    return result


def image_response(data, path):
    mime = IMAGE_TYPES.get(PurePosixPath(path).suffix.lower())
    if not mime:
        raise Http404
    content = unpack(data).get(path)
    if content is None:
        raise Http404
    response = HttpResponse(content, content_type=mime)
    response['Content-Disposition'] = content_disposition_header(False, PurePosixPath(path).name)
    response['X-Content-Type-Options'] = 'nosniff'
    # SVG diagrams often use inline styles. Scripts and external resources
    # remain forbidden, including when someone opens the image URL directly.
    response['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    response['Cache-Control'] = 'private, no-store'
    return response
