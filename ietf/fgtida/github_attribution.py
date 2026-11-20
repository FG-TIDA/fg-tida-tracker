"""Read public attribution fields without treating GitHub accounts as tracker identities."""
import re
from itertools import takewhile
from .credit_lists import source_credit_list, MAX_CREDITS


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

ORG_ROLE = re.compile(r'\b(?:university|institute|lab|laboratory|group|groupe|company|corporation|technologies|services|solutions|consulting|LLC|Ltd|GmbH|Inc|chair|expert|MBA|MSc|PhD|director|fellow|independent|individual|authority|commission|centre|center|forum|foundation|section|invited|program|committee)\b', re.I)


def person_line(value):
    words = value.split()
    return (2 <= len(words) <= 5 and not ORG_ROLE.search(value) and not re.search(r'[/#:=]', value)
        and all(word[0].isupper() or word.casefold() in {'de', 'del', 'van', 'von', 'da', 'al', 'bin'} for word in words))


def proposer_credits(value):
    """Read the combined GitHub proposer field, not arbitrary narrative names."""
    names, organizations = [], []
    for row in value.splitlines():
        row = row.strip().removeprefix('- ').removeprefix('* ')
        if not row or re.match(r'^\(?https?://', row):
            continue
        continuation = re.match(r'^(?:AI Expert|MBA|MSc|PhD|Section Leader|Co-Chair|Invited Expert|Visiting Research Fellow|Chair\b|Centre\b|Center\b|University\b|Institute\b|Department\b|School\b)', row, re.I)
        if names and (continuation or (ORG_ROLE.search(row) and not person_line(row) and ',' not in row.split('(', 1)[0])):
            if not organizations:
                organizations.append(row)
            continue
        paired = re.fullmatch(r'(.+?)\s*\(([^()]+)\)(?:[.,;]\s+.*)?', row)
        if paired:
            possible = source_credit_list(paired[1])
            if len(possible) == 1 or all(person_line(name) for name in possible):
                names.extend(plain_value(name) for name in possible if plain_value(name))
                organizations.append(plain_value(paired[2]))
                continue
        slash = re.split(r'\s*/\s*|\s+[-–—]\s+', row, maxsplit=1)
        if len(slash) == 2 and all(person_line(name) for name in source_credit_list(slash[0])):
            names.extend(plain_value(name) for name in source_credit_list(slash[0]) if plain_value(name))
            organizations.append(plain_value(slash[1]))
            continue
        comma = source_credit_list(row)
        if len(comma) >= 2:
            # This field's established format is Name, Organization, location.
            # Several people are separate rows; Authors fields use CSV lists.
            names.append(plain_value(comma[0]))
            rest = comma[1:]
            while rest and re.match(r'^(?:Esq\.?|Chair|Managing Director|Visiting Research Fellow)(?:\s|$)', rest[0], re.I):
                rest.pop(0)
            organization = ', '.join(rest)
            if re.search(r'\)\.\s+[A-Z]', organization):
                organization = re.split(r'\)\.\s+(?=[A-Z])', organization, maxsplit=1)[0] + ')'
            if organization := plain_value(organization):
                organizations.append(organization)
        elif not names or person_line(row) or row.startswith('@'):
            if name := plain_value(row):
                names.append(name)
        elif ORG_ROLE.search(row) and not organizations:
            organizations.append(plain_value(row))
    return list(filter(None, names)), list(filter(None, organizations))


def plain_value(value, max_length=350):
    value = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', value)
    value = EMAIL.sub('', value).strip(' \t|*_.,;–—-\u200b\u200c\u200d\ufeff')
    value = re.sub(r'<\s*>|\(\s*\)', '', value).strip()
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
    return value[:max_length] if max_length is not None else value


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
                if following.startswith(('- ', '* ')):
                    bullets = []
                    for row in lines[index+1:]:
                        row = row.strip()
                        if not row:
                            continue
                        if FIELD.fullmatch(row) or not row.startswith(('- ', '* ')):
                            break
                        bullets.append(row[2:])
                    value = '\n'.join(bullets)
                elif 'proposer' in match['label'].casefold():
                    people = []
                    for row in lines[index+1:]:
                        row = row.strip()
                        if not row and not people:
                            continue
                        if (not row or row.startswith('#') or FIELD.fullmatch(row) or RST_FIELD.fullmatch(row)
                                or re.fullmatch(r'(?:summary|scope|motivation|description|rationale|references|problem statement)', row, re.I)):
                            break
                        people.append(row)
                    value = '\n'.join(people)
        label = match['label'].casefold()
        if value and (re.fullmatch(AUTHOR, label) or label in {'contact', 'contacts'}) and re.match(r'(?i)^\s*Name\s*:', value):
            parts = list(identity.finditer(value))
            for pos, part in enumerate(parts):
                end = parts[pos+1].start() if pos+1 < len(parts) else len(value)
                declared = value[part.end():end].strip(' /|;')
                key = part[1].casefold()
                if key == 'name' and (name := plain_value(declared)):
                    (contacts if label in {'contact', 'contacts'} else authors).append(name)
                elif key in ('organization', 'organisation') and (organization := plain_value(declared)):
                    affiliations.append(organization)
                elif key in ('email', 'e-mail'):
                    contact_emails.extend(EMAIL.findall(declared))
            continue
        if label.startswith(('contact email', 'contact e-mail')):
            contact_emails.extend(EMAIL.findall(value or ''))
        elif label in {'contact', 'contacts'}:
            contact_emails.extend(EMAIL.findall(value or ''))
            entries = source_credit_list(value or '') if label == 'contacts' else [value or '']
            contacts.extend(name for entry in entries if (name := plain_value(entry)))
        elif value := plain_value(value or '', max_length=None):
            if re.fullmatch(AFFILIATION, label):
                if re.fullmatch(r'individual expert(?: contribution)?(?:\s*\([^()]+\))?', value, re.IGNORECASE):
                    value = 'Individual expert'
                entries = source_credit_list(value) if label.endswith(('s', '(s)')) else [value]
                affiliations.extend(organization for entry in entries if (organization := plain_value(entry)))
            elif label.startswith('contact name'):
                entries = source_credit_list(value) if label.endswith(('s', '(s)')) else [value]
                contacts.extend(name for entry in entries if (name := plain_value(entry)))
            elif 'proposer' in label:
                people, organizations = proposer_credits(value)
                authors.extend(people)
                affiliations.extend(organizations)
            else:
                if label == 'author' and ' / ' in value:
                    name, organization = value.split(' / ', 1)
                    if re.search(r'\b(independent|expert|researcher|laboratory|institute|university|academy|LLC|Ltd)\b', organization, re.I):
                        value = name
                        affiliations.append(organization)
                if label.startswith('drafter') and '@' in value:
                    authors.extend(re.findall(r'(?<!\w)@[A-Za-z0-9][A-Za-z0-9-]{0,38}', value))
                    continue
                entries = source_credit_list(value) if label.endswith(('s', '(s)')) or '\n' in value else [value]
                authors.extend(clean for entry in entries if (clean := plain_value(entry)))
    return {key: list(dict.fromkeys(values))[:MAX_CREDITS] for key, values in zip(
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
        if re.match(r'(?i)^Name\s*:', name):
            identity = document_attribution('Author: ' + name)
            authors.extend(identity.get('authors', []))
            affiliations.extend(identity.get('affiliations', []))
        elif value := plain_value(name):
            authors.append(value)
        if plain_value(name):
            if not organization.startswith('<') and (affiliation := plain_value(organization)):
                affiliations.append(affiliation)
    return {'authors': list(dict.fromkeys(authors))[:MAX_CREDITS], 'affiliations': list(dict.fromkeys(affiliations))[:MAX_CREDITS]}


def source_attribution(body):
    values = document_attribution(body)
    values.update({key: value for key, value in rst_table_attribution(body).items() if value})
    return {key: value for key, value in values.items() if value}


def structured_attribution(metadata):
    authors, affiliations = [], []
    entries = metadata.get('authors', [])
    for entry in entries[:MAX_CREDITS] if isinstance(entries, list) else []:
        if isinstance(entry, str):
            authors.append(plain_value(entry))
        elif isinstance(entry, dict):
            name, affiliation = entry.get('name'), entry.get('affiliation')
            if isinstance(name, str):
                authors.append(plain_value(name))
            if isinstance(affiliation, str):
                affiliations.append(plain_value(affiliation))
    return {'authors': list(filter(None, authors)), 'affiliations': list(filter(None, affiliations))}
