from pathlib import Path
import re
from urllib.parse import urlsplit
from django import template
from django.urls import reverse
from django.utils.html import format_html
from ietf.fgtida import workflow, core
from django.utils.html import format_html_join

register = template.Library()


@register.simple_tag(name='public_version')
def version_tag(document,revision):
    from ietf.fgtida.versioning import labels
    return labels(document).get(revision,'—')


@register.filter
def document_version(document):
    from ietf.fgtida.versioning import public_version
    return public_version(document)


@register.inclusion_tag('fgtida/session_actions.html')
def session_actions(session):
    groups = {key: [] for key in ('notes', 'recording', 'pdf', 'pptx', 'other')}
    docs = getattr(session, 'public_materials', None)
    if docs is None:
        docs = core.documents().filter(session=session)
    for doc in docs:
        if doc.type_id == 'agenda':
            continue  # Meeting agendas remain available in the Materials section.
        if doc.type_id == 'minutes':
            groups['notes'].append((doc.title, reverse('fgtida:document', args=[doc.name])))
        if doc.type_id == 'recording' and doc.external_url:
            groups['recording'].append((doc.title, doc.external_url))
        if doc.uploaded_filename and Path(doc.get_file_name()).is_file():
            suffix = Path(doc.uploaded_filename).suffix.lower()
            key = {'.pdf': 'pdf', '.pptx': 'pptx', '.ppt': 'pptx'}.get(suffix, 'other')
            groups[key].append((doc.title, reverse('fgtida:download', args=[doc.name, doc.rev])))
    for resource in session.tida_resources.all():
        suffix = Path(resource.filename).suffix.lower()
        key = resource.kind if resource.kind in ('notes', 'recording') else {'.pdf': 'pdf', '.pptx': 'pptx', '.ppt': 'pptx'}.get(suffix, 'other')
        if resource.kind != 'login':
            groups[key].append((resource.title, resource.resource_url))
    categories = [('notes', 'Session notes / summary', 'chat-left-text'),
        ('recording', 'Session recording', 'camera-video'), ('pdf', 'PDF materials', 'file-earmark-pdf'),
        ('pptx', 'PowerPoint presentations', 'file-earmark-ppt'), ('other', 'Other materials', 'file-earmark-text')]
    actions = [{'key': key, 'label': label, 'icon': icon, 'links': groups[key]} for key, label, icon in categories
        if key != 'other' or groups[key]]
    return {'actions': actions, 'session': session}


@register.simple_tag
def account_person(user):
    from django.core.exceptions import ObjectDoesNotExist
    if not getattr(user, 'is_authenticated', False):
        return None
    try:
        return user.person
    except ObjectDoesNotExist:
        return None


@register.simple_tag
def document_type(doc):
    return dict(core.TYPE_LABELS).get(doc.type_id, doc.type.name)


@register.simple_tag
def session_status(session):
    status = workflow.session_status(session)
    return format_html('<span class="badge rounded-pill text-bg-{}">{}</span>', status['color'], status['label'])


@register.simple_tag
def meeting_status(meeting):
    status = workflow.meeting_status(meeting)
    return format_html('<span class="badge rounded-pill text-bg-{}">{}</span>', status['color'], status['label'])


@register.simple_tag
def document_states(doc):
    return format_html_join(' ', '<span class="badge rounded-pill text-bg-{}">{}</span>',
        ((workflow.DOCUMENT_COLORS.get(state.slug, 'secondary'), state.name) for state in doc.states.all())) or 'Not set'


@register.simple_tag
def document_recency(doc):
    label = workflow.recent_marker(doc)
    return format_html('<span class="badge text-bg-{}" title="Activity in the last 14 days">{}</span>',
        'success' if label == 'New' else 'info', label) if label else ''


@register.simple_tag
def document_categories(doc):
    categories = dict(core.CATEGORIES)
    return format_html_join(' ', '<a class="badge text-bg-light" href="{}">{}</a>',
        ((reverse('fgtida:documents') + '?category=' + tag.slug[5:], categories[tag.slug[5:]])
         for tag in doc.tags.all() if tag.slug.startswith('tida-') and tag.slug[5:] in categories)) or 'Not classified'


@register.filter
def rich_text(value, source_url=''):
    """Render complete Markdown blocks using Datatracker's HTML sanitizer."""
    # Imported prose can contain visually blank lines made of non-breaking
    # spaces. Markdown treats those as paragraph text, swallowing following tables.
    text = re.sub(r'(?m)^[^\S\r\n]+$', '', str(value or ''))
    # GitHub issues often place a table directly after a paragraph. Normalize
    # only actual table delimiters, leaving fenced examples untouched.
    lines=text.splitlines()
    normalized=[]
    fenced=False
    for index,line in enumerate(lines):
        if line.lstrip().startswith(('```','~~~')):
            fenced=not fenced
        if (not fenced and index+1<len(lines) and '|' in line
                and re.fullmatch(r'\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*',lines[index+1])
                and normalized and normalized[-1].strip()):
            normalized.append('')
        if not fenced:
            line = re.sub(r'^(\s*>\s*)\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$', lambda match: match[1] + '**' + match[2].capitalize() + '**', line)
        normalized.append(line)
    text='\n'.join(normalized)
    from ietf.fgtida.rendering import markdown_fragment
    from django.utils.safestring import mark_safe
    return format_html('<div class="fg-tida-rich-text">{}</div>', mark_safe(markdown_fragment(text, source_url)))


@register.filter
def rich_rst(value, source_url=''):
    import hashlib
    import subprocess
    import sys
    from django.core.cache import cache
    from django.utils.safestring import mark_safe
    text=str(value or '')
    if len(text.encode())>500000:
        return format_html('<pre>{}</pre>',text)
    from ietf.fgtida.rendering import clean_fragment
    key='fgtida:rst:v2:'+hashlib.sha256(text.encode()).hexdigest()
    rendered=cache.get(key)
    if rendered is None:
        try:
            result=subprocess.run([sys.executable,'-m','ietf.fgtida.rst_worker'],input=text.encode(),
                capture_output=True,check=True,timeout=15)
            rendered=clean_fragment(result.stdout.decode())
            if len(rendered.encode())<500000:
                cache.set(key,rendered,3600)
        except (subprocess.SubprocessError,UnicodeError):
            return format_html('<pre>{}</pre>',text)
    return format_html('<div class="fg-tida-rich-text">{}</div>',mark_safe(clean_fragment(rendered, source_url)))


@register.simple_tag
def group_number(group):
    from django.core.exceptions import ObjectDoesNotExist
    try:
        return f'wg{group.tida_configuration.number}'
    except ObjectDoesNotExist:
        return ''


@register.filter
def group_label(group):
    if group.acronym == 'tida':
        return 'FG-TIDA (no wg assigned)'
    if group.acronym == 'incubation':
        return 'No working group yet'
    number=group_number(group)
    if number:
        acronym = group.tida_configuration.topic_acronym.upper()
        return f'{number.upper()} ({acronym}) : {group.name}' if acronym else f'{number.upper()} : {group.name}'
    return group.name


@register.filter
def file_type(doc):
    extension = Path(doc.uploaded_filename or '').suffix.lower()
    return {'.docx': 'Word', '.pdf': 'PDF', '.zip': 'ZIP package'}.get(extension, extension.lstrip('.').upper() or 'Not uploaded')


@register.inclusion_tag('fgtida/formats.html')
def document_formats(doc, alternatives_only=False, plain=False):
    links = []
    name = doc.doc.name if doc.is_dochistory() else doc.name
    if not alternatives_only and doc.uploaded_filename and doc.rev and Path(doc.get_file_name()).is_file():
        extension = Path(doc.uploaded_filename).suffix.lower().lstrip('.')
        icon = {'pdf': 'file-pdf', 'txt': 'file-text', 'md': 'file-text', 'xml': 'file-code',
                'docx': 'file-word', 'pptx': 'file-slides'}.get(extension, 'file-earmark')
        from ietf.fgtida.versioning import public_version
        links.append(('Word' if extension == 'docx' else extension.upper(), icon, reverse('fgtida:download', args=[name, public_version(doc)])))
        if extension != 'txt' and Path(doc.get_file_name()).with_suffix('.txt').is_file():
            links.append(('Text', 'file-text', reverse('fgtida:text', args=[name, public_version(doc)])))
    original = doc.doc if doc.is_dochistory() else doc
    for alternative in original.tida_formats.all():
        if alternative.revision == doc.rev:
            label = alternative.get_kind_display()
            if Path(doc.uploaded_filename or '').suffix == '.' + alternative.kind:
                label += ' (alternative)'
            links.append((label, 'file-pdf' if alternative.kind == 'pdf' else 'file-word', reverse('fgtida:document-format', args=[alternative.pk])))
    if not alternatives_only and doc.external_url and urlsplit(doc.external_url).scheme in ('http', 'https'):
        label, icon = ('Recording', 'camera-video') if doc.type_id == 'recording' else ('Template package', 'folder') if doc.type_id == 'tida-template' else ('External reference', 'box-arrow-up-right')
        links.append((label, icon, doc.external_url))
    return {'format_links': links, 'plain': plain, 'alternatives_only': alternatives_only}
