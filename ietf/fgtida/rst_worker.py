"""Render public RST text with no file access, raw HTML, or unbounded parser work."""
import io
import re
import sys


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(10,10))
    from docutils.core import publish_parts
    text=sys.stdin.buffer.read(500001).decode('utf-8')
    if len(text.encode())>500000:
        raise ValueError('RST display limit exceeded')
    lines=[]
    skipping=False
    for line in text.splitlines():
        if re.match(r'^\.\.\s+(raw|include|toctree|literalinclude|automodule|autoclass|openapi)::',line):
            skipping=True
            continue
        if skipping:
            if not line.strip() or line[:1].isspace():
                continue
            skipping=False
        lines.append(line)
    parts=publish_parts('\n'.join(lines),writer_name='html5',settings_overrides={
        'file_insertion_enabled':False,'raw_enabled':False,'report_level':5,'halt_level':6,
        'warning_stream':io.StringIO(),'syntax_highlight':'none','embed_stylesheet':False})
    output=parts['fragment'].encode()
    if len(output)>1000000:
        raise ValueError('Rendered display limit exceeded')
    sys.stdout.buffer.write(output)


if __name__=='__main__':
    main()
