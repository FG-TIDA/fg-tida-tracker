"""Bounded GET-only capacity probe. Local by default; never prints page contents."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--clients', type=int, default=10)
    parser.add_argument('--requests', type=int, default=50)
    parser.add_argument('--path', action='append', default=[])
    parser.add_argument('--allow-remote', action='store_true', help='Explicitly permit testing your own staging service. Do not run against the live meeting site.')
    args = parser.parse_args()
    base = urlsplit(args.base_url)
    if base.scheme not in {'http', 'https'} or base.username or base.password or base.query or base.fragment:
        parser.error('Use an HTTP(S) origin without credentials, query or fragment.')
    if base.hostname not in {'localhost', '127.0.0.1', '::1'} and not args.allow_remote:
        parser.error('Remote testing requires --allow-remote and an authorized staging service.')
    if not 1 <= args.clients <= 300 or not args.clients <= args.requests <= 3000:
        parser.error('Use 1-300 clients and clients <= requests <= 3000.')
    paths = args.path or ['/health/', '/doc/']
    if any(not path.startswith(('/doc/', '/group/', '/meeting/', '/health/')) or path.startswith('//') or '://' in path for path in paths):
        parser.error('Only public document/group/meeting/health paths are permitted.')

    def read(index):
        started = time.monotonic()
        try:
            request = Request(args.base_url.rstrip('/') + paths[index % len(paths)], headers={'User-Agent': 'FG-TIDA-staging-capacity-check'})
            with urlopen(request, timeout=15) as response:
                status, size = response.status, 0
                while chunk := response.read(65536):
                    size += len(chunk)
                    if size > 25 * 1024 * 1024:
                        raise ValueError('Response size limit')
            return time.monotonic() - started, status < 400
        except Exception:
            return time.monotonic() - started, False

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.clients) as pool:
        results = list(pool.map(read, range(args.requests)))
    elapsed = time.monotonic() - started
    times = sorted(duration for duration, ok in results)
    print(json.dumps({'clients': args.clients, 'requests': len(results), 'errors': sum(not ok for _, ok in results),
        'elapsed_seconds': round(elapsed, 3), 'requests_per_second': round(len(results) / elapsed, 2),
        'p95_seconds': round(times[min(len(times) - 1, int(len(times) * .95))], 3), 'max_seconds': round(times[-1], 3)}, indent=2))


if __name__ == '__main__':
    main()
