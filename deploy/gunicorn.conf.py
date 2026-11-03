import os

bind = '0.0.0.0:' + os.environ.get('PORT', '8000')
workers = int(os.environ.get('WEB_CONCURRENCY', '3'))
timeout = 45
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
# nginx owns access logs and redacts signed account/recovery paths.
accesslog = None
errorlog = '-'
capture_output = True
control_socket_disable = True
# Import Django once before workers start, avoiding cold imports within a request timeout.
preload_app = True


def on_starting(server):
    # Compile lazy URL/template registries in the master, before worker timeouts
    # apply. A broken template fails startup rather than the first real request.
    from django.urls import get_resolver
    from django.template.loader import get_template
    get_resolver().url_patterns
    for template in ('fgtida/home.html', 'fgtida/agenda.html', 'fgtida/meeting_materials.html', 'fgtida/mfa.html', 'admin/base.html'):
        get_template(template)
forwarded_allow_ips = '*'  # The container is only reachable on the private proxy network.
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190
