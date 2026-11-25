"""Offline specification front-matter checks; no uploaded configuration executes.

Completeness is not technical approval, proof of authorship or malware safety.
"""
import re
from django.core.exceptions import ValidationError

TEMPLATE_URL = 'https://github.com/FG-TIDA/spec-template/blob/main/src/doc/index.rst'
PROFILE = 'spec-front-matter-2026-11-25'
SECTIONS = ('Abstract', 'History', 'Authors', 'Keywords', 'Foreword')
PLACEHOLDER = re.compile(
    r'lorem(?:\s|,\s*)+ipsum|Not supplied\s*[—-]\s*administrator review required|'
    r'<(?:version|title|date|acronym|Jane Doe|John Smith|Organization / Company|[^<>\n]*@example\.com)>', re.I)
EXAMPLE_TEXT = re.compile(r'lorem(?:\s|,\s*)+ipsum|<(?:Jane Doe|John Smith|Organization / Company|[^<>\n]*@example\.com)>', re.I)


def sections(text):
    """Read unindented RST headings, ignoring prose mentions and code blocks."""
    lines = text.splitlines()
    headings = []
    for index, line in enumerate(lines[:-1]):
        if not line.strip() or line != line.lstrip() or line.startswith('.. '):
            continue
        underline = lines[index + 1].strip()
        if re.fullmatch(r'([=~^+#*\-])\1{2,}', underline) and len(underline) >= len(line.rstrip()):
            label = re.sub(r'^\d+(?:\.\d+)*[.)]?\s+', '', line.strip())
            label = {name.casefold(): name for name in SECTIONS}.get(label.casefold(), label)
            label = {'author(s)': 'Authors', 'author': 'Authors'}.get(label.casefold(), label)
            headings.append((index, label))
    blocks = {}
    for position, (start, label) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        blocks.setdefault(label, []).append('\n'.join(lines[start + 2:end]).strip())
    return headings, blocks


def substantive(body):
    lines = [line for line in body.splitlines() if not re.match(r'^\s*(?:\.\.|:[\w-]+:)', line)]
    words = re.findall(r'[\w@]+', '\n'.join(lines), re.UNICODE)
    headers = {'name', 'affiliation', 'contact', 'version', 'document', 'approval', 'focus', 'group', 'working',
               'header', 'rows', 'widths', 'tida'}
    return any(word.casefold() not in headers for word in words)


def problems(text):
    from .versioning import declared_version
    headings, blocks = sections(text)
    issues = []
    if not headings or headings[0][1] in SECTIONS or headings[0][1].casefold() in {
            'title of this document', '<title>', 'title'}:
        issues.append('Add your actual document title as a heading before Abstract.')
    if headings:
        front_start = next((index for index, label in headings if label == 'Abstract'), headings[0][0])
        prefix = '\n'.join(text.splitlines()[:front_start])
        if re.search(r'(?m)^\s*(?:\.\.\s+(?:\|[^|]+\|\s+)?(?:image|figure|list-table|csv-table)::|[+|])', prefix):
            issues.append('Start with the document title, not logos or cover tables. Put supporting figures and tables in the document body.')
    positions = []
    for label in SECTIONS:
        bodies = blocks.get(label, [])
        if not bodies:
            issues.append(f'Add the {label} heading and complete its content.')
            continue
        positions.append(next(index for index, name in headings if name == label))
        if len(bodies) != 1:
            issues.append(f'Keep one {label} front-matter section.')
        body = bodies[0]
        if not substantive(body) or PLACEHOLDER.search(body) or re.fullmatch(r'TBD|TBC|TODO|N/?A', body.strip(), re.I):
            issues.append(f'Complete {label}; remove empty cells, instructions and template placeholders.')
        if label == 'History' and not declared_version(body):
            issues.append('History must include a document version in X.Y notation, e.g. 0.0. Do not invent approval dates.')
    if positions != sorted(positions):
        issues.append('Use this front-matter order: ' + ', '.join(SECTIONS) + '.')
    return issues


def check_archive(archive, manifest, *, word=False):
    from .source_packages import unpack
    files = unpack(archive)
    # Validate the converted Word body, never its generated navigation wrapper.
    entry = manifest['entry_document']
    # The Word converter produces a navigation-only index. Also allow that
    # same generated package to be edited and uploaded again as an RST ZIP.
    if 'chapters/01_contribution.rst' in files:
        index = files.get(entry, b'').decode('utf-8-sig')
        body = re.sub(r'(?m)^\.\. toctree::[^\n]*\n(?:(?:[ \t]+[^\n]*)?\n)*', '', index)
        nonempty = [line for line in body.splitlines() if line.strip()]
        if word or (len(nonempty) == 2 and re.fullmatch(r'([=~^+#*\-])\1{2,}', nonempty[1])):
            entry = 'chapters/01_contribution.rst'
    if entry not in files:
        raise ValidationError('The template entry document is missing.')
    issues = problems(files[entry].decode('utf-8-sig'))
    # Catch unedited examples in chapters and optional front matter as well.
    if any(EXAMPLE_TEXT.search(data.decode('utf-8-sig')) for name, data in files.items() if name.endswith('.rst')):
        issues.append('Replace example text and placeholders throughout the RST source, including optional sections and chapters.')
    if issues:
        raise ValidationError(['Follow the FG-TIDA specification template before submitting.'] + issues + [TEMPLATE_URL])
    manifest['template_validation'] = {'profile': PROFILE, 'entry_document': entry}
    return manifest
