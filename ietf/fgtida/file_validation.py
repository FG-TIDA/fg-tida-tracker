"""Structural validation shared by the bounded Office and source parsers."""
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit

from defusedxml import ElementTree
from django.core.exceptions import ValidationError


def validate_office(data, extension):
    from .source_packages import unpack
    files = unpack(data)
    part = 'word/document.xml' if extension == '.docx' else 'ppt/presentation.xml'
    if part not in files or '[Content_Types].xml' not in files:
        raise ValidationError('Upload a complete, valid DOCX or PPTX file.')
    content_types = ElementTree.fromstring(files['[Content_Types].xml'])
    expected = ('application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
                if extension == '.docx' else 'application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml')
    if not any(node.get('PartName') == '/' + part and node.get('ContentType') == expected for node in content_types):
        raise ValidationError('The Office document type does not match its extension.')
    if any(any(marker in node.get('ContentType', '').casefold() for marker in ('macroenabled', 'vbaproject', 'oleobject', 'activex')) for node in content_types):
        raise ValidationError('Macro-enabled Office documents and embedded objects are not accepted.')
    for name, value in files.items():
        lower = name.casefold()
        if (lower.endswith(('.bin', '.exe', '.dll', '.com', '.js', '.vbs', '.ps1')) or
                any(piece in {'embeddings', 'activex', 'customui'} for piece in PurePosixPath(lower).parts)):
            raise ValidationError('Office files containing macros, embedded objects or executable content are not accepted.')
        if lower.endswith('.rels'):
            for relationship in ElementTree.fromstring(value):
                kind = relationship.get('Type', '').rsplit('/', 1)[-1].casefold()
                if kind in {'oleobject', 'package', 'attachedtemplate', 'vbaproject', 'control'}:
                    raise ValidationError('Embedded objects and attached Office templates are not accepted.')
                if relationship.get('TargetMode', '').casefold() == 'external':
                    if kind != 'hyperlink':
                        raise ValidationError('Embed images and other media inside the Office file before uploading.')
                    if urlsplit(relationship.get('Target', '')).scheme.casefold() not in {'http', 'https', 'mailto'}:
                        raise ValidationError('Office hyperlinks must use HTTP, HTTPS or email addresses.')
        if lower.endswith('.xml'):
            instructions = []
            for node in ElementTree.fromstring(value).iter():
                if node.tag.rsplit('}', 1)[-1].casefold() in {'object', 'oleobject', 'altchunk'}:
                    raise ValidationError('Embedded document objects are not accepted.')
                if node.tag.rsplit('}', 1)[-1] == 'instrText':
                    instructions.append(node.text or '')
                instructions.extend(item for key, item in node.attrib.items() if key.rsplit('}', 1)[-1] == 'instr')
            if re.search(r'\b(DDE(?:AUTO)?|INCLUDETEXT|INCLUDEPICTURE|DATABASE|LINK)\b', ''.join(instructions), re.IGNORECASE):
                raise ValidationError('Office fields that execute commands or load external content are not accepted.')
    return files


def validate_passive_pdf(reader):
    from pypdf.generic import DictionaryObject, ArrayObject, IndirectObject
    pending = [reader.trailer]
    seen = set()
    count = 0
    while pending:
        node = pending.pop()
        count += 1
        if count > 100000:
            raise ValidationError('This PDF is too complex to inspect safely.')
        if isinstance(node, IndirectObject):
            identity = (node.idnum, node.generation)
            if identity in seen:
                continue
            seen.add(identity)
            node = node.get_object()
        if isinstance(node, DictionaryObject):
            if set(node).intersection({'/JS', '/JavaScript', '/OpenAction', '/AA', '/EmbeddedFiles', '/XFA', '/RichMediaContent'}):
                raise ValidationError('PDF scripts, automatic actions, embedded files and active forms are not accepted.')
            if node.get('/S') in {'/JavaScript', '/Launch', '/SubmitForm', '/ImportData', '/GoToR', '/GoToE'} or node.get('/Type') == '/EmbeddedFile':
                raise ValidationError('Active PDF content is not accepted.')
            if node.get('/S') == '/URI' and urlsplit(str(node.get('/URI', ''))).scheme.casefold() not in {'http', 'https', 'mailto'}:
                raise ValidationError('PDF links must use HTTP, HTTPS or email addresses.')
            pending.extend(node.values())
        elif isinstance(node, ArrayObject):
            pending.extend(node)
