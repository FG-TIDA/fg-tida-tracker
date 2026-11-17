"""Read public attribution fields without treating GitHub accounts as tracker identities."""
import re
from itertools import takewhile


AUTHOR = r'authors?(?:\(s\))?|proposers?(?:\(s\))?|contributors?(?:\(s\))?|drafters?|contact name'
AFFILIATION = r'affiliations?(?:\(s\))?|submitting organi[sz]ation'
FIELDS = rf'{AUTHOR}|{AFFILIATION}|contact e-?mail(?: address)?'
CREDIT_FIELDS = ('authors', 'affiliations', 'contacts', 'contact_emails')
FIELD = re.compile(
    rf'^\s*(?:(?P<heading>#{{1,6}})\s+|[-*]\s+)?\**(?P<label>{FIELDS})\**\s*'
    r'(?::\s*\**\s*(?P<value>.*))?$', re.IGNORECASE,
)
RST_FIELD = re.compile(rf'^\s*:(?P<label>{FIELDS}):\s*(?P<value>.*)$', re.IGNORECASE)
EMAIL = re.compile(r'[\w.!#$%&\x27*+/=?^_`{|}~-]+@[\w.-]+\.[a-z]{2,}', re.IGNORECASE)


def plain_value(value):
    value = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', value)
    value = EMAIL.sub('', value).strip(' \t*_.,;–—-\u200b\u200c\u200d\ufeff')
    if value.startswith(('<', '@')) or value.casefold() in {'none', 'n/a', 'tbd', 'tbc', 'no response'}:
        return ''
    return value[:350]


def document_attribution(body):
    authors, affiliations, contacts, contact_emails = [], [], [], []
    lines = re.sub(r'<!--.*?-->', '', body[:65536], flags=re.DOTALL).splitlines()
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
            if match.groupdict().get('heading') and not following.startswith('#') and not FIELD.fullmatch(following):
                value = following.removeprefix('- ')
        label = match['label'].casefold()
        if label.startswith(('contact email', 'contact e-mail')):
            contact_emails.extend(EMAIL.findall(value or ''))
        elif value := plain_value(value or ''):
            if re.fullmatch(AFFILIATION, label):
                if re.fullmatch(r'individual expert(?: contribution)?(?:\s*\([^()]+\))?', value, re.IGNORECASE):
                    value = 'Individual expert'
                affiliations.append(value)
            elif label == 'contact name':
                contacts.append(value)
            else:
                if label.startswith('proposer'):
                    # Themes commonly use "Name / Organization" or
                    # "Name - Organization - email" in the Proposer field.
                    parts = re.split(r'\s*/\s*|\s+[-–—]\s+', value, maxsplit=1)
                    if len(parts) == 1 and value.count(',') == 1:
                        parts = [part.strip() for part in value.split(',')]
                    if len(parts) == 2:
                        value, organization = parts
                        affiliations.append(organization)
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
