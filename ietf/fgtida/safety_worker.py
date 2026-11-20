"""Inspect untrusted file structure in a bounded process with no document execution."""
import io
from pathlib import Path
import sys


def inspect_publication(data, kind):
    """Publication checks are separate from optional preview compatibility."""
    if kind in {'pdf', 'doc', 'docx', 'pptx', 'xml', 'txt', 'md', 'rst', 'zip', 'repository'}:
        if kind in {'docx', 'pptx', 'zip', 'repository'}:
            import zipfile
            # Bound archives without requiring their contents to be renderable.
            # Invalid containers remain opaque downloads; preview parsers reject them.
            if zipfile.is_zipfile(io.BytesIO(data)):
                from .source_packages import unpack
                unpack(data)
        return
    inspect(data, kind)


def inspect(data, kind):
    from .files import _extract_text
    if kind == 'repository':
        from .source_packages import unpack
        unpack(data)
    elif kind == 'tar.gz':
        from .github_archives import _validate
        _validate(data)
    elif kind in {'pdf', 'docx', 'pptx', 'xml', 'txt', 'md', 'rst'}:
        _extract_text(data, 'file.' + kind)
    elif kind == 'zip':
        from .source_packages import unpack
        files = unpack(data)
        _extract_text(data, 'source.zip')
        for name, content in files.items():
            suffix = Path(name).suffix.lower().lstrip('.')
            if suffix in {'pdf', 'docx', 'pptx', 'svg', 'png', 'jpg', 'jpeg', 'gif'}:
                inspect(content, suffix)
    elif kind == 'svg':
        from defusedxml import ElementTree
        tree = ElementTree.fromstring(data)
        for node in tree.iter():
            if node.tag.rsplit('}', 1)[-1].casefold() in {'script', 'foreignobject', 'iframe', 'object', 'embed'}:
                raise ValueError('Active SVG content')
            for name, value in node.attrib.items():
                if name.rsplit('}', 1)[-1].casefold().startswith('on') or (
                    name.rsplit('}', 1)[-1].casefold() in {'href', 'src'} and not value.startswith('#')):
                    raise ValueError('External SVG content')
    elif kind in {'png', 'jpg', 'jpeg', 'gif'}:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 25000000:
                raise ValueError('Image too large')
            image.verify()
    elif kind in {'mp4', 'm4a', 'webm', 'mp3', 'wav'}:
        header = data[:16]
        valid = {'mp4': header[4:8] == b'ftyp', 'm4a': header[4:8] == b'ftyp',
                 'webm': header.startswith(b'\x1aE\xdf\xa3'),
                 'mp3': header.startswith(b'ID3') or (len(header) >= 2 and header[0] == 255 and header[1] & 224 == 224),
                 'wav': header[:4] == b'RIFF' and header[8:12] == b'WAVE'}
        if not valid[kind]:
            raise ValueError('Invalid media signature')
    else:
        raise ValueError('Unsupported file type')


if __name__ == '__main__':
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    data = sys.stdin.buffer.read(100 * 1024 * 1024 + 1)
    if not data or len(data) > 100 * 1024 * 1024:
        raise ValueError('Invalid file size')
    if len(sys.argv) > 2 and sys.argv[2] == 'publication':
        inspect_publication(data, sys.argv[1])
    else:
        inspect(data, sys.argv[1])
