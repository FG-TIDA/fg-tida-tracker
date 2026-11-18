"""Pinned specification-template data; its configuration is never executed here."""
import ast
import base64
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

PROFILE = 'word-spec-template-v1'


@lru_cache(maxsize=1)
def scaffold():
    value = json.loads(Path(__file__).with_name('spec_scaffold.json').read_text(encoding='utf-8'))
    files = {}
    for name, item in value['files'].items():
        data = base64.b64decode(item['data'], validate=True)
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Specification scaffold checksum mismatch.')
        files[name] = data
    return value['commit'], files


def approved_static_file(name, data):
    # Only these exact reviewed assets may use normally prohibited active-file
    # extensions. They are stored, never served inline or executed by previews.
    if name not in {'_static/osia-sidebar.js', '_templates/page.html'}:
        return False
    return scaffold()[1].get(name) == data


def wrap_word_source(text, media):
    commit, original = scaffold()
    files = dict(original)
    lines = text.splitlines()
    title = 'FG-TIDA contribution'
    for index in range(len(lines) - 1):
        if lines[index].strip() and re.fullmatch(r'([=~^+#*-])\1{2,}', lines[index + 1].strip()):
            title = lines[index].strip()[:200]
            break
    # Adapt simple metadata assignments without importing or executing conf.py.
    config = files['conf.py'].decode('utf-8').splitlines(keepends=True)
    versions = re.findall(r'(?im)^\s*(?:\|\s*|\*\*|:)?(?:document\s+|charter\s+)?version\s*(?:\*\*)?\s*[:|]\s*(?:\*\*)?\s*v?(\d{1,6}\.\d{1,6})\b', text)
    release = max(versions, key=lambda value: tuple(map(int, value.split('.')))) if versions else '0.0'
    values = {'project': title, 'html_title': title, 'release': release, 'author': '',
              'cover_title': 'FG-TIDA contribution', 'cover_series': 'Focus Group contribution',
              'cover_subject': title, 'cover_subtitle': 'Converted from Word', 'copyright': ''}
    for node in reversed(ast.parse(''.join(config)).body):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in values:
            config[node.lineno - 1:node.end_lineno] = [node.targets[0].id + ' = ' + repr(values[node.targets[0].id]) + '\n']
    files['conf.py'] = ''.join(config).encode()
    paths = {name: 'images/word/' + name.removeprefix('media/') for name in media}
    pattern = re.compile(r'(?m)^(\s*\.\.\s+(?:\|[^|\n]+\|\s+)?(?:image|figure)::[ \t]*)([^\n]+?)\s*$')
    text = pattern.sub(lambda match: match[1] + '../' + paths[match[2].strip()]
                       if match[2].strip() in paths else match[0], text)
    files['chapters/01_contribution.rst'] = text.encode('utf-8')
    files['index.rst'] = (title + '\n' + '=' * max(3, len(title))
        + '\n\n.. toctree::\n   :maxdepth: 3\n\n   chapters/01_contribution\n').encode('utf-8')
    files.update({paths[name]: data for name, data in media.items()})
    files['conversion.json'] = (json.dumps({'profile': PROFILE, 'template_repository': 'FG-TIDA/spec-template',
        'template_commit': commit, 'entry_document': 'index.rst'}, indent=2) + '\n').encode()
    files['README.md'] = (
        '# Converted FG-TIDA contribution\n\n'
        'The Word content is in chapters/01_contribution.rst and extracted media in images/word/.\n'
        'index.rst links the chapter. This package includes the reviewed specification-template\n'
        'configuration, styling, layout, logos and dependency list. Template example chapters\n'
        'are intentionally replaced by the actual contribution.\n\n'
        'Review all text, tables, equations and figures against the Word original. Edit the\n'
        'metadata in conf.py before publication. This package does not imply WG approval.\n\n'
        'To build in a separate local environment: install requirements.txt, then run\n'
        '`python -m sphinx -b html . _build/html`. PDF builds also need a LaTeX toolchain.\n'
        'The tracker never executes conf.py, template JavaScript or build dependencies.\n\n'
        f'Template: https://github.com/FG-TIDA/spec-template/tree/{commit}\n'
        'Original template license: LICENSE.txt.\n').encode()
    return files
