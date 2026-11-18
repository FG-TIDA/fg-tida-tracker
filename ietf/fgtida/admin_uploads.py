"""Scope administrator trust to bytes actually uploaded in this request."""
from contextvars import ContextVar
import hashlib

from .file_safety import MAX_SCAN_BYTES

_uploads = ContextVar('fgtida_admin_uploads', default=None)


def actor_for_digest(digest):
    values = _uploads.get()
    return values.get(digest) if values else None


class TrustedAdminUploadsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        trusted = {}
        if request.method == 'POST' and user and user.is_active and user.is_staff:
            for uploads in request.FILES.lists():
                for upload in uploads[1]:
                    if not 0 < upload.size <= MAX_SCAN_BYTES:
                        continue
                    position = upload.tell()
                    digest = hashlib.sha256()
                    try:
                        for chunk in upload.chunks():
                            digest.update(chunk)
                        trusted[digest.hexdigest()] = user
                    finally:
                        upload.seek(position)
        token = _uploads.set(trusted)
        try:
            return self.get_response(request)
        finally:
            _uploads.reset(token)
