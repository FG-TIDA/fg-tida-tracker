"""Bounded document-version parsing, also usable by isolated Word conversion."""
import re


def _row_versions(rows):
    """Read the explicitly named version column, including blank other cells."""
    column = None
    for row in rows:
        cells = [' '.join(cell.replace('*', '').replace('`', '').split()) for cell in row]
        headers = [cell.casefold() for cell in cells]
        named = next((index for index, cell in enumerate(headers)
                      if cell in {'version', 'document version', 'charter version'}), None)
        if column is None and named is not None:
            column = named
        elif column is not None and column < len(cells):
            match = re.fullmatch(r'v?(\d{1,6}\.\d{1,6})', cells[column], re.I)
            if match:
                yield match[1]


def _table_versions(text):
    """Bounded, inert parsing of Pandoc grid/simple tables and pipe tables.

    Never execute RST directives or build configuration to find a version.
    """
    lines = text.splitlines()
    rows, pending, columns = [], [], []
    for line in lines + ['']:
        line = line.strip()
        border = bool(re.fullmatch(r'\+(?:[-=]+\+)+', line))
        if border:
            next_columns = [match.start() for match in re.finditer(r'\+', line)]
            if len(next_columns) > 65:
                continue
            if columns and columns != next_columns:
                yield from _row_versions(rows)
                rows, pending = [], []
            if pending:
                rows.append([' '.join(parts) for parts in zip(*pending)])
                pending = []
            columns = next_columns
        elif columns and line.startswith('|') and len(line) > columns[-1]:
            pending.append([line[left + 1:right].strip() for left, right in zip(columns, columns[1:])])
        else:
            if pending:
                rows.append([' '.join(parts) for parts in zip(*pending)])
            yield from _row_versions(rows)
            rows, pending, columns = [], [], []

    # Markdown/pipe tables can put Version in a column other than the first.
    rows = []
    for line in lines + ['']:
        if line.strip().startswith('|') and line.strip().endswith('|'):
            rows.append(line.strip()[1:-1].split('|'))
        else:
            yield from _row_versions(rows)
            rows = []

    # RST simple tables use whitespace-aligned columns separated by '=' runs.
    rows, spans, header_separator = [], [], False
    for line in lines + ['']:
        if re.fullmatch(r'\s*=+(?:\s+=+)+\s*', line):
            next_spans = [(match.start(), match.end()) for match in re.finditer(r'=+', line)]
            if not 2 <= len(next_spans) <= 64:
                continue
            if spans == next_spans and not header_separator:
                header_separator = True
            elif spans == next_spans:
                yield from _row_versions(rows)
                rows, spans, header_separator = [], [], False
            else:
                yield from _row_versions(rows)
                rows, spans, header_separator = [], next_spans, False
        elif spans and line.strip():
            rows.append([line[left:right].strip() for left, right in spans])
        else:
            yield from _row_versions(rows)
            rows, spans, header_separator = [], [], False


def _declared_version_in_block(text):
    """Read explicit version fields and version-history tables, never dates or section numbers."""
    values = []
    prefix = text[:65536]
    values.extend(_table_versions(prefix))
    field = r'(?:charter\s+|document\s+)?version'
    for match in re.finditer(rf'(?im)^\s*(?:\|\s*|\*\*|:)?{field}\s*(?:\*\*)?\s*[:|]\s*(?:\*\*)?\s*v?(\d+\.\d+)\b', prefix):
        values.append(match[1])
    values.extend(re.findall(rf'(?im)^[ \t]*{field}[ \t]*:?[ \t]*\n[ \t]*v?(\d+\.\d+)[ \t]*(?:\n|$)', prefix))
    for match in re.finditer(r'(?im)^\s*\*\s+-\s+Version\s*\n(?P<table>(?:[ \t]+[^\n]*\n|\s*\n)+)', prefix):
        values.extend(re.findall(r'(?m)^\s*\*\s+-\s+v?(\d+\.\d+)\s*$', match['table']))
    for match in re.finditer(r'(?im)^\s*\|\s*Version\s*\|[^\n]*\n\s*\|[- :|]+\|\s*\n(?P<table>(?:\s*\|[^\n]*\n?)+)', prefix):
        values.extend(re.findall(r'(?m)^\s*\|\s*v?(\d+\.\d+)\s*\|', match['table']))
    valid = [value for value in values if re.fullmatch(r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})', value)]
    return max(valid, key=lambda value: tuple(map(int, value.split('.')))) if valid else None


def declared_version(text):
    """Prefer document History over version columns in technical/API tables."""
    lines = text[:65536].splitlines()
    headings = []
    for index, line in enumerate(lines[:-1]):
        if line.strip() and line == line.lstrip() and not line.startswith('.. '):
            underline = lines[index + 1].strip()
            if re.fullmatch(r'([=~^+#*\-])\1{2,}', underline) and len(underline) >= len(line.rstrip()):
                label = re.sub(r'^\d+(?:\.\d+)*[.)]?\s+', '', line.strip()).casefold()
                headings.append((index, label))
    for position, (start, label) in enumerate(headings):
        if label in {'history', 'version history', 'document history', 'revision history'}:
            end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
            version = _declared_version_in_block('\n'.join(lines[start + 2:end]))
            if version is not None:
                return version
    return _declared_version_in_block(text)
