"""Resource-limited parser; invoked only with an extension and stdin bytes."""
import sys


def main():
    if sys.platform != 'win32':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    from .files import MAX_BYTES, _extract_text
    text = _extract_text(sys.stdin.buffer.read(MAX_BYTES + 1), 'upload' + sys.argv[1])
    sys.stdout.buffer.write(text.encode('utf-8'))


if __name__ == '__main__':
    main()
