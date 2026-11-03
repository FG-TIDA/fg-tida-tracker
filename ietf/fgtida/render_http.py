"""Health checks bypass HTTPS redirects, but still validate the Host header."""
from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponsePermanentRedirect


class RenderEdgeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host()  # ALLOWED_HOSTS applies even to health probes.
        if request.path_info == '/health/':
            if request.method not in {'GET', 'HEAD'}:
                return HttpResponse(status=405)
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT 1')
                    cursor.fetchone()
            except DatabaseError:
                return HttpResponse('Unavailable', status=503, headers={'Cache-Control': 'no-store'})
            return HttpResponse('OK', headers={'Cache-Control': 'no-store'})
        if host.lower() != settings.FGTIDA_PUBLIC_DOMAIN.lower():
            if request.method not in {'GET', 'HEAD'}:
                return HttpResponseBadRequest('Use the canonical HTTPS domain.')
            return HttpResponsePermanentRedirect(settings.SITE_URL + request.get_full_path())
        return self.get_response(request)
