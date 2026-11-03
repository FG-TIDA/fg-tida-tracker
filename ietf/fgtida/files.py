import io
import os
import zipfile
import subprocess
import sys
from defusedxml import ElementTree
from django.core.exceptions import ValidationError

MAX_BYTES = 10 * 1024 * 1024
EXTENSIONS = {'.txt', '.md', '.xml', '.pdf', '.docx', '.pptx'}


def extract_text(data, filename):
    if len(data) > MAX_BYTES or os.path.splitext(filename)[1].lower() not in EXTENSIONS:
        raise ValidationError('Use a PDF, DOCX, PPTX, TXT, Markdown or XML file up to 10 MB.')
    # Compressed PDF/Office data may expand far beyond upload size. Parsing is
    # bounded in a separate process so a malformed file cannot exhaust a worker.
    if os.path.splitext(filename)[1].lower() in {'.pdf', '.docx', '.pptx', '.xml'}:
        try:
            result = subprocess.run([sys.executable, '-m', 'ietf.fgtida.extract_worker',
                                     os.path.splitext(filename)[1].lower()],
                                    input=data, capture_output=True, timeout=15, check=True)
            return result.stdout.decode('utf-8')[:2_000_000]
        except (subprocess.SubprocessError, UnicodeError) as error:
            raise ValidationError('Cannot safely read this file. Choose a valid, unencrypted document.') from error
    return _extract_text(data, filename)


def _extract_text(data, filename):
    if len(data) > MAX_BYTES or os.path.splitext(filename)[1].lower() not in EXTENSIONS:
        raise ValidationError('Use a PDF, DOCX, PPTX, TXT, Markdown or XML file up to 10 MB.')
    extension = os.path.splitext(filename)[1].lower()
    try:
        if extension in {'.txt', '.md'}:
            text = data.decode('utf-8-sig')
        elif extension == '.xml':
            text = ' '.join(ElementTree.fromstring(data).itertext())
        elif extension in {'.docx', '.pptx'}:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if len(archive.infolist()) > 5000 or sum(item.file_size for item in archive.infolist()) > 30 * 1024 * 1024:
                    raise ValueError('Oversized Office archive')
                entries = [item for item in archive.infolist() if (
                    item.filename == 'word/document.xml' if extension == '.docx' else
                    item.filename.startswith('ppt/slides/slide') and item.filename.endswith('.xml'))]
                if not entries or sum(item.file_size for item in entries) > 30 * 1024 * 1024:
                    raise ValueError('Invalid Office file')
                text = '\n'.join(' '.join(ElementTree.fromstring(archive.read(item)).itertext()) for item in entries)
        else:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted or len(reader.pages) > 1000:
                raise ValueError('Encrypted or oversized PDF')
            text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    except Exception as error:
        raise ValidationError('Cannot read this file. Choose a valid, unencrypted document.') from error
    return text.replace('\x00', '')[:2_000_000]
