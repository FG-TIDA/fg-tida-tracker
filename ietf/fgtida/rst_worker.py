"""Render public RST text with no file access, raw HTML, or unbounded parser work."""
import io
import re
import sys


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(10,10))
    from docutils.core import publish_parts
    from docutils import nodes
    from docutils.parsers.rst import Directive, directives, roles
    class Only(Directive):
        required_arguments = 1
        final_argument_whitespace = True
        has_content = True
        def run(self):
            if self.arguments[0].strip() not in {'html', 'not latex'}:
                return []
            container = nodes.container()
            self.state.nested_parse(self.content, self.content_offset, container)
            return list(container.children)
    directives.register_directive('only', Only)
    class Diagram(Directive):
        optional_arguments = 1
        final_argument_whitespace = True
        has_content = True
        option_spec = {name: directives.unchanged for name in (
            'caption', 'alt', 'name', 'align', 'width', 'height', 'scale', 'filename',
            'format', 'class', 'figwidth', 'figclass', 'zoom', 'config')}
        def run(self):
            message = nodes.note('', nodes.paragraph(text='Diagram source is shown below. Include a PNG or safe SVG export for a visual preview; uploaded diagram programs are not executed.'))
            source = '\n'.join(self.content)
            output = [message]
            if self.arguments:
                output.append(nodes.paragraph(text='Diagram: ' + self.arguments[0]))
            if self.options.get('caption'):
                output.append(nodes.paragraph(text=self.options['caption']))
            if source:
                output.append(nodes.literal_block(source, source))
            return output
    for name in ('mermaid', 'graphviz', 'uml', 'plantuml', 'drawio'):
        directives.register_directive(name, Diagram)
    def reference_role(name, rawtext, text, lineno, inliner, options=None, content=None):
        match = re.fullmatch(r'(.+?)\s*<([^>]+)>', text)
        title, target = match.groups() if match else (text, text)
        return [nodes.reference(rawtext, title, refuri='#'+nodes.make_id(target))], []
    roles.register_local_role('ref', reference_role)
    def literal_role(name, rawtext, text, lineno, inliner, options=None, content=None):
        return [nodes.literal(rawtext, text)], []
    for name in ['doc', 'term', 'class', 'func', 'mod']:
        roles.register_local_role(name, literal_role)
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
