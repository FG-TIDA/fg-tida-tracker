"""Maintenance-only WSGI app: no Django, files, accounts or database exposure."""


def application(environ, start_response):
    healthy = environ.get('PATH_INFO') == '/health/' and environ.get('REQUEST_METHOD') in {'GET', 'HEAD'}
    body = b'Maintenance mode: data import in progress.\n'
    status = '200 OK' if healthy else '503 Service Unavailable'
    headers = [('Content-Type', 'text/plain; charset=utf-8'), ('Cache-Control', 'no-store'),
               ('X-Content-Type-Options', 'nosniff'), ('Content-Length', str(len(body)))]
    if not healthy:
        headers.append(('Retry-After', '300'))
    start_response(status, headers)
    return [] if environ.get('REQUEST_METHOD') == 'HEAD' else [body]
