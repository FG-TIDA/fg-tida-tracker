"""Public document rendering with table structure and a bounded HTML allowlist."""
from urllib.parse import urljoin, urlsplit
from html import escape
from html.parser import HTMLParser
import re
import bleach
import markdown
from ietf.utils.html import acceptable_tags


def allowed_attribute(tag, name, value):
    if name == 'id':
        return True
    if tag == 'a' and name in {'href', 'title'}:
        return True
    if tag in {'td', 'th'} and name in {'colspan', 'rowspan'}:
        return value.isdecimal() and 1 <= int(value) <= 1000
    if tag == 'th' and name == 'scope':
        return value in {'row', 'col', 'rowgroup', 'colgroup'}
    if tag == 'ol' and name == 'start':
        return value.isdecimal() and len(value) <= 6
    if name == 'class':
        return all(part in {'fg-atomic', 'admonition', 'admonition-title', 'note', 'warning', 'important', 'tip', 'caution', 'literal-block', 'docutils', 'align-default', 'simple'} for part in value.split())
    return False


def clean_fragment(fragment, source_url=''):
    cleaner = bleach.Cleaner(tags=acceptable_tags - {'style', 'head', 'body', 'html', 'title', 'font'},
        attributes=allowed_attribute, protocols={'http', 'https', 'mailto'}, strip=True)
    fragment = cleaner.clean(fragment)
    def resolve(attrs, new=False):
        href = attrs.get((None, 'href'), '')
        if href and not href.startswith('#') and not urlsplit(href).scheme and urlsplit(source_url).scheme in {'http', 'https'}:
            attrs[(None, 'href')] = urljoin(source_url, href)
        return attrs
    # Linkify text nodes only. Linker's skip_tags path double-escapes entities
    # inside code examples; preserving entity tokens keeps examples faithful.
    class Links(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.parts, self.literal, self.anchor = [], 0, 0
            self.linker = bleach.linkifier.Linker(callbacks=[resolve])
        def handle_starttag(self, tag, attrs):
            self.literal += tag in {'pre', 'code'}
            self.anchor += tag == 'a'
            values = dict(attrs)
            if tag == 'a' and values.get('href'):
                values['href'] = resolve({(None, 'href'): values['href']})[(None, 'href')]
            self.parts.append('<'+tag+''.join(' '+key+'="'+escape(value or '', quote=True)+'"' for key, value in values.items())+'>')
        def handle_endtag(self, tag):
            self.literal = max(0, self.literal - (tag in {'pre', 'code'}))
            self.anchor = max(0, self.anchor - (tag == 'a'))
            self.parts.append('</'+tag+'>')
        def handle_data(self, data):
            text = escape(data, quote=False)
            if not self.literal and re.fullmatch(r'\s*(?:\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:\s*(?:UTC|GMT|Z))?)?|v?\d+(?:\.\d+){1,2})\s*', data):
                self.parts.append('<span class="fg-atomic">'+text+'</span>')
            else:
                self.parts.append(self.linker.linkify(text) if not self.literal and not self.anchor and ('://' in data or 'www.' in data) else text)
        def handle_entityref(self, name):
            self.parts.append('&'+name+';')
        def handle_charref(self, name):
            self.parts.append('&#'+name+';')
    parser = Links()
    parser.feed(fragment)
    parser.close()
    return cleaner.clean(''.join(parser.parts))


def markdown_fragment(text, source_url=''):
    # Soft-wrapped source paragraphs should not acquire a <br> on every line.
    return clean_fragment(markdown.markdown(text, extensions=['extra', 'sane_lists', 'toc']), source_url)
