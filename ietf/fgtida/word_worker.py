"""Isolated DOCX conversion: bounded resources, Pandoc sandbox, no external media."""
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    resource.setrlimit(resource.RLIMIT_FSIZE, (30 * 1024 * 1024,) * 2)
    from defusedxml import ElementTree
    from .source_packages import unpack, pack, MAX_UPLOAD
    data = sys.stdin.buffer.read(MAX_UPLOAD + 1)
    files = unpack(data)
    if 'word/document.xml' not in files:
        raise ValueError('A DOCX document is required')
    for name, value in files.items():
        if name.startswith('word/') and name.endswith('.xml'):
            document = ElementTree.fromstring(value)
            if any(node.tag.rsplit('}', 1)[-1] in {'ins', 'del', 'moveFrom', 'moveTo', 'altChunk'} for node in document.iter()):
                raise ValueError('Resolve tracked changes and embedded document objects before submission')
        if name.endswith('.rels'):
            for node in ElementTree.fromstring(value):
                if node.get('TargetMode') == 'External' and not node.get('Type', '').endswith('/hyperlink'):
                    raise ValueError('Embed external media before submission')
    with tempfile.TemporaryDirectory(prefix='tida-word-') as directory:
        root = Path(directory)
        (root / 'input.docx').write_bytes(data)
        subprocess.run(['pandoc', '--sandbox', '--from=docx', '--to=rst', '--standalone', '--wrap=none',
                        '--extract-media=media', '--output=index.rst', 'input.docx'],
                       cwd=root, check=True, timeout=25, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        output = {'index.rst': (root / 'index.rst').read_bytes()}
        for path in (root / 'media').rglob('*'):
            if path.is_file():
                output[path.relative_to(root).as_posix()] = path.read_bytes()
        sys.stdout.buffer.write(pack(output))


if __name__ == '__main__':
    main()
