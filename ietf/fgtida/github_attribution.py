"""Read public attribution fields without treating GitHub accounts as tracker identities."""
import re
from itertools import takewhile


AUTHOR = r'authors?(?:\(s\))?|(?:theme )?proposers?(?:\(s\))?|contributors?(?:\(s\))?|drafters?|contact names?(?:\(s\))?'
AFFILIATION = r'affiliations?(?:\(s\))?|(?:submitting )?organi[sz]ations?(?:\(s\))?'
FIELDS = rf'{AUTHOR}|{AFFILIATION}|contact e-?mails?(?: addresses?)?|contacts?'
CREDIT_FIELDS = ('authors', 'affiliations', 'contacts', 'contact_emails')
FIELD = re.compile(
    rf'^\s*(?:(?P<heading>#{{1,6}})\s+|[-*]\s+)?\**(?P<label>{FIELDS})\**\s*'
    r'(?::\s*\**\s*(?P<value>.*))?$', re.IGNORECASE,
)
RST_FIELD = re.compile(rf'^\s*:(?P<label>{FIELDS}):\s*(?P<value>.*)$', re.IGNORECASE)
EMAIL = re.compile(r'[\w.!#$%&\x27*+/=?^_`{|}~-]+@[\w.-]+\.[a-z]{2,}', re.IGNORECASE)


def plain_value(value):
    value = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', value)
    value = EMAIL.sub('', value).strip(' \t|*_.,;–—-\u200b\u200c\u200d\ufeff')
    if not re.search(r'[^\W\d_]', value, re.UNICODE):
        return ''  # Markdown/RST separators and empty table cells are not people.
    if value.startswith('<') and value.endswith('>') and '<' not in value[1:-1] and '>' not in value[1:-1]:
        value = value[1:-1].strip(' \t*_.,;–—-')
    if value.startswith('<') or value.casefold() in {'none', 'n/a', 'tbd', 'tbc', 'no response', 'summary',
            'not entered', 'not provided', 'unknown', 'see submission contact below', '(system)',
            'name', 'your name', 'contact name', 'organization', 'your organization', 'affiliation',
            'name(s)', 'name(s) and affiliation(s)', 'name and affiliation', 'name / organization',
            'name / affiliation', 'your full name', 'first last', 'first name last name',
            'author name', 'org', '@your-github-username'} or re.fullmatch(r'author\s*\d+', value, re.I):
        return ''
    return value[:350]


def document_attribution(body):
    authors, affiliations, contacts, contact_emails = [], [], [], []
    prefix = body[:65536]
    # One legacy issue template placed the proposer name in the heading itself.
    prefix = re.sub(r'(?mi)^#{1,6}\s+([^\n]+)\n\s*<!--\s*Name\(s\) and affiliation\(s\) of whoever',
                    r'## Proposer(s)\n\n\1\n<!-- Name(s) and affiliation(s) of whoever', prefix)
    text = re.sub(r'<!--.*?-->', '', prefix, flags=re.DOTALL)
    text = re.sub(rf'(?mi)^(\s*\*\*(?:{FIELDS}))\*\*[ \t]+(?=\S)', r'\1:** ', text)
    # A proposer field may contain a complete labelled identity, rather than
    # just a name. Split its fields before matching so country/email never
    # become part of the author. Only normalize Name inside this explicit block.
    identity = re.compile(r'(?i)\b(Name|Organization|Organisation|Country|E-mail|Email)\s*:')
    normalized = []
    identity_block = False
    for row in text.splitlines():
        unmarked = re.sub(r'\*\*', '', row).strip().lstrip('>- ').strip()
        if re.match(rf'(?i)(?:{AUTHOR})\s*:', unmarked):
            identity_block = True
        elif unmarked.startswith('#'):
            identity_block = bool(re.search(r'(?i)identification|proposer|author|contact', unmarked))
        if identity_block and identity.search(unmarked):
            matches = list(identity.finditer(unmarked))
            # Do not interpret "Contact name:" twice or arbitrary prose.
            if matches and (matches[0].start() == 0 or re.fullmatch(rf'(?:{AUTHOR})\s*:\s*', unmarked[:matches[0].start()], re.I)):
                labels = {'name': 'Author', 'organization': 'Organization', 'organisation': 'Organization',
                          'country': 'Country', 'e-mail': 'Contact email', 'email': 'Contact email'}
                for pos, match in enumerate(matches):
                    end = matches[pos+1].start() if pos+1 < len(matches) else len(unmarked)
                    normalized.append(f"{labels[match[1].lower()]}: {unmarked[match.end():end].strip(' /|;')}")
                continue
        normalized.append(row)
    text = '\n'.join(normalized)
    # Some submissions omit the colon after a bold metadata label.
    text = re.sub(rf'(?mi)^(\s*\*\*(?:{FIELDS}))\*\*[ \t]+(?=\S)', r'\1:** ', text)
    lines = text.splitlines()
    for index, row in enumerate(lines):
        lines[index] = row = re.sub(r'^\s*>\s*', '', row)
        source = re.fullmatch(r'\*\*Source:\*\*\s+([^,\n]+),\s*(.+)', row)
        if source and any(re.match(r'\*\*Contact', line.strip(), re.I) for line in lines[index+1:index+4]):
            lines[index] = row = f'**Proposer:** {source[1]} / {source[2]}'
        if row.strip().startswith('|'):
            cells = [cell.strip().strip('*').strip() for cell in row.strip().strip('|').split('|')]
            if len(cells) == 2 and re.fullmatch(FIELDS, cells[0].rstrip(':'), re.IGNORECASE):
                lines[index] = f'**{cells[0].rstrip(":")}:** {cells[1]}'
    fence = ''
    for index, line in enumerate(lines):
        marker = line.lstrip()[:3]
        if marker in {'```', '~~~'} and (not fence or marker == fence):
            fence = '' if fence else marker
            continue
        if fence:
            continue
        match = FIELD.fullmatch(line) or RST_FIELD.fullmatch(line)
        if not match:
            continue
        value = match['value']
        if not value:
            following = next((row.strip() for row in lines[index + 1:] if row.strip()), '')
            if not following.startswith('#') and not FIELD.fullmatch(following):
                value = following.removeprefix('- ')
        label = match['label'].casefold()
        if value and re.fullmatch(AUTHOR, label) and re.match(r'(?i)^\s*Name\s*:', value):
            parts = list(identity.finditer(value))
            for pos, part in enumerate(parts):
                end = parts[pos+1].start() if pos+1 < len(parts) else len(value)
                declared = value[part.end():end].strip(' /|;')
                key = part[1].casefold()
                if key == 'name' and (name := plain_value(declared)):
                    authors.append(name)
                elif key in ('organization', 'organisation') and (organization := plain_value(declared)):
                    affiliations.append(organization)
                elif key in ('email', 'e-mail'):
                    contact_emails.extend(EMAIL.findall(declared))
            continue
        if label.startswith(('contact email', 'contact e-mail')):
            contact_emails.extend(EMAIL.findall(value or ''))
        elif label in {'contact', 'contacts'}:
            contact_emails.extend(EMAIL.findall(value or ''))
            if name := plain_value(value or ''):
                contacts.append(name)
        elif value := plain_value(value or ''):
            if re.fullmatch(AFFILIATION, label):
                if re.fullmatch(r'individual expert(?: contribution)?(?:\s*\([^()]+\))?', value, re.IGNORECASE):
                    value = 'Individual expert'
                affiliations.append(value)
            elif label.startswith('contact name'):
                contacts.append(value)
            else:
                if label == 'author' and ' / ' in value:
                    name, organization = value.split(' / ', 1)
                    if re.search(r'\b(independent|expert|researcher|laboratory|institute|university|academy|LLC|Ltd)\b', organization, re.I):
                        value = name
                        affiliations.append(organization)
                if label.startswith('theme proposer'):
                    match = re.fullmatch(r'(.+?)\s*\(([^()]+)\)', value)
                    if match:
                        authors.extend(clean for name in match[1].split(',') if (clean := plain_value(name)))
                        if organization := plain_value(match[2]):
                            affiliations.append(organization)
                        continue
                if label.startswith('proposer') or label.startswith('theme proposer'):
                    # Themes commonly use "Name / Organization" or
                    # "Name - Organization - email" in the Proposer field.
                    parts = re.split(r'\s*/\s*|\s+[-–—]\s+', value, maxsplit=1)
                    if len(parts) == 1 and value.count(',') == 1:
                        parts = [part.strip() for part in value.split(',')]
                    if len(parts) == 2:
                        value, organization = parts
                        if organization := plain_value(organization):
                            affiliations.append(organization)
                if value := plain_value(value):
                    authors.append(value)
    return {key: list(dict.fromkeys(values))[:20] for key, values in zip(
        CREDIT_FIELDS, (authors or contacts, affiliations, contacts, contact_emails))}


def rst_table_attribution(body):
    section = re.search(r'(?mi)^Authors?[ \t]*\n[-=~]+[ \t]*\n', body[:65536])
    if not section:
        return {}
    lines = body[section.end():].splitlines()
    rows = '\n'.join(takewhile(lambda line: not line or line[0].isspace() or line.startswith('.. list-table::'), lines))
    authors, affiliations = [], []
    for name, organization in re.findall(r'(?m)^ +\* - ([^\n]+)\n + - ([^\n]+)', rows):
        name, organization = name.strip(), organization.strip()
        if name == 'Name' or name.startswith('<'):
            continue  # The template uses angle brackets for sample values.
        if value := plain_value(name):
            authors.append(value)
            if not organization.startswith('<') and (affiliation := plain_value(organization)):
                affiliations.append(affiliation)
    return {'authors': list(dict.fromkeys(authors))[:20], 'affiliations': list(dict.fromkeys(affiliations))[:20]}


def source_attribution(body):
    values = document_attribution(body)
    values.update({key: value for key, value in rst_table_attribution(body).items() if value})
    return {key: value for key, value in values.items() if value}


def structured_attribution(metadata):
    authors, affiliations = [], []
    entries = metadata.get('authors', [])
    for entry in entries[:20] if isinstance(entries, list) else []:
        if isinstance(entry, str):
            authors.append(plain_value(entry))
        elif isinstance(entry, dict):
            name, affiliation = entry.get('name'), entry.get('affiliation')
            if isinstance(name, str):
                authors.append(plain_value(name))
            if isinstance(affiliation, str):
                affiliations.append(plain_value(affiliation))
    return {'authors': list(filter(None, authors)), 'affiliations': list(filter(None, affiliations))}
