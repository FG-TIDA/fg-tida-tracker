"""Preview scanned package images without extracting files to public storage."""
import posixpath
import re
from pathlib import PurePosixPath

from django.http import Http404, HttpResponse
from django.utils.http import content_disposition_header

from .source_packages import unpack, rst_chapters

IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
               '.gif': 'image/gif', '.svg': 'image/svg+xml'}
IMAGE_DIRECTIVE = re.compile(r'(?m)^([ \t]*\.\.[ \t]+(?:\|[^|\n]+\|[ \t]+)?(?:image|figure)::[ \t]*)([^\n]+?)[ \t]*$')
INCLUDE_DIRECTIVE = re.compile(r'^([ \t]*)\.\.[ \t]+include::[ \t]*(\S+)[ \t]*$')
DIAGRAM_DIRECTIVE = re.compile(r'^([ \t]*)\.\.[ \t]+(mermaid|graphviz|uml|plantuml|drawio)::[ \t]*(.*)$')


def local_path(name, target, files):
    path = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    if target.startswith('/') or ':' in target or '\\' in target or path not in files:
        return None
    return path


def chapters(data, asset_url):
    """Resolve each image against its chapter, preserving nested source paths.

    Call only after the archive has passed file_safety checks. The URL builder
    identifies either the private contribution or a particular public revision.
    """
    files = unpack(data)
    result = []
    entries = list(rst_chapters(files))
    included = set()
    for name, content in entries:
        for line in content.splitlines():
            match = INCLUDE_DIRECTIVE.match(line)
            if match:
                target = local_path(name, match[2], files)
                if target and target.endswith('.rst'):
                    included.add(target)
    roots = [(name, content) for name, content in entries if name not in included]
    seen = set()
    # Include unvisited sections too: an isolated include cycle or unsupported
    # include options must not make an entire chapter silently disappear.
    for name, content in roots + [(name, content) for name, content in entries if name in included]:
        if name in seen:
            continue
        image_urls = set()
        budget = [0]
        def expand(current, content, parents=()):
            seen.add(current)
            if current in parents or len(parents) >= 12:
                return '\n.. warning:: An included section could not be displayed because its include chain is cyclic or too deep.\n'
            budget[0] += len(content.encode())
            if budget[0] > 500000:
                return '\n.. warning:: An included section exceeds the reading-preview size limit. Use the source package for the full content.\n'
            output = []
            lines = content.splitlines(keepends=True)
            index = 0
            while index < len(lines):
                line = lines[index]
                diagram = DIAGRAM_DIRECTIVE.match(line.rstrip('\r\n'))
                if diagram:
                    end = index + 1
                    while end < len(lines) and (not lines[end].strip() or len(lines[end])-len(lines[end].lstrip()) > len(diagram[1])):
                        end += 1
                    block = ''.join(lines[index+1:end])
                    target = diagram[3].strip()
                    path = local_path(current, target, files) if target else None
                    export = None
                    if path:
                        candidates = [path] if PurePosixPath(path).suffix.lower() in IMAGE_TYPES else [str(PurePosixPath(path).with_suffix(suffix)) for suffix in ('.svg', '.png')] + [path + '.svg', path + '.png']
                        export = next((candidate for candidate in candidates if candidate in files), None)
                    if export:
                        url = asset_url(export)
                        image_urls.add(url)
                        caption = re.search(r'(?m)^\s*:caption:\s*(.+)$', block)
                        alt = re.search(r'(?m)^\s*:alt:\s*(.+)$', block)
                        output.append(diagram[1] + '.. figure:: ' + url + '\n')
                        if alt:
                            output.append(diagram[1] + '   :alt: ' + alt[1] + '\n')
                        output.append('\n')
                        if caption:
                            output.append(diagram[1] + '   ' + caption[1] + '\n\n')
                    else:
                        output.extend(lines[index:end])
                        if path and PurePosixPath(path).suffix.lower() in {'.mmd', '.puml', '.drawio', '.txt'}:
                            budget[0] += len(files[path])
                            if budget[0] <= 500000:
                                source = files[path].decode('utf-8-sig', errors='replace')
                                output.append('\n' + ''.join(diagram[1] + '   ' + part + '\n' for part in source.splitlines()) + '\n')
                    index = end
                    continue
                include = INCLUDE_DIRECTIVE.match(line.rstrip('\r\n'))
                if include:
                    target = local_path(current, include[2], files)
                    # Never enable docutils filesystem access. Resolve only inert
                    # package text, in memory, retaining the included file's base.
                    if target and PurePosixPath(target).suffix.lower() in {'.rst', '.txt'}:
                        has_options = index + 1 < len(lines) and lines[index+1].lstrip().startswith(':')
                        if not has_options:
                            nested = expand(target, files[target].decode('utf-8-sig', errors='replace'), (*parents, current))
                            output.append('\n' + ''.join(include[1] + part if part.strip() else part for part in nested.splitlines(keepends=True)) + '\n')
                            index += 1
                            continue
                    output.append(include[1] + '.. warning::\n\n' + include[1] + '   This included section cannot be previewed safely. Check its path and include options in the source package.\n')
                    index += 1
                    continue
                def replace(match):
                    return image(current, match)
                output.append(IMAGE_DIRECTIVE.sub(replace, line))
                index += 1
            return ''.join(output)
        def image(current, match):
            target = match[2].strip()
            path = local_path(current, target, files)
            if not path or PurePosixPath(path).suffix.lower() not in IMAGE_TYPES:
                indent = re.match(r'[ \t]*', match[1])[0]
                return indent + '.. warning::\n\n' + indent + '   A referenced visual is unavailable in this preview. Include a PNG, JPEG, GIF or safe SVG export in the source package.\n'
            url = asset_url(path)
            image_urls.add(url)
            return match[1] + url
        text = expand(name, content)
        result.append((name, text, {'image_urls': image_urls}))
    return result


def image_response(data, path):
    mime = IMAGE_TYPES.get(PurePosixPath(path).suffix.lower())
    if not mime:
        raise Http404
    content = unpack(data).get(path)
    if content is None:
        raise Http404
    # Download permission for the ZIP does not grant permission to render any
    # particular image. Keep active SVG and image resource checks at this boundary.
    from .file_safety import inspect_file
    from django.core.exceptions import ValidationError, PermissionDenied
    try:
        import hashlib
        from django.core.cache import cache
        kind = PurePosixPath(path).suffix.lower().lstrip('.')
        key = 'fgtida:preview-image:v1:' + kind + ':' + hashlib.sha256(content).hexdigest()
        if cache.get(key) is not True:
            inspect_file(content, kind)
            cache.set(key, True, 3600)
    except ValidationError as error:
        raise PermissionDenied('This image cannot be previewed safely.') from error
    response = HttpResponse(content, content_type=mime)
    response['Content-Disposition'] = content_disposition_header(False, PurePosixPath(path).name)
    response['X-Content-Type-Options'] = 'nosniff'
    # SVG diagrams often use inline styles. Scripts and external resources
    # remain forbidden, including when someone opens the image URL directly.
    response['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    response['Cache-Control'] = 'private, no-store'
    return response
