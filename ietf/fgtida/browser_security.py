"""Browser restrictions for the FG-TIDA HTML interface."""


# Bootstrap and existing templates use inline styles. Scripts, unlike styles,
# must be served as files from this origin: no inline handlers or eval().
# The inherited base template loads its fonts from this one explicit host.
POLICY = '; '.join((
    "default-src 'self'",
    "script-src 'self'",
    "script-src-attr 'none'",
    "style-src 'self' 'unsafe-inline' https://static.ietf.org",
    "font-src 'self' data: https://static.ietf.org",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "media-src 'self' blob:",
    "frame-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    'upgrade-insecure-requests',
))


class BrowserSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get('Content-Type', '').split(';', 1)[0].strip().lower()
        # Native PDF viewers and media streams must keep their own handling.
        if content_type in {'text/html', 'application/xhtml+xml'}:
            response['Content-Security-Policy'] = POLICY
            response['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        return response
