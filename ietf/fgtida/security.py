"""Explicit publication boundary for this installation of the upstream platform."""
from django.conf import settings
from django.http import HttpResponseNotFound, HttpResponsePermanentRedirect
from django.urls import resolve, Resolver404
from django.utils.deprecation import MiddlewareMixin


# Native URLs remain registered for reverse() in upstream templates, but only
# reviewed workflows are reachable. Never use a menu as an authorization boundary.
ADMIN_MODELS = {
    'fgtida': {'contribution', 'contributionrecipient', 'sessionresource', 'outgoingmail', 'securityquestion'},
    'auth': {'user', 'group'},
    'person': {'person', 'email', 'alias'},
    'name': {'importantdatename'},
    'doc': {'document'},
    'group': {'group', 'role', 'groupmilestone', 'groupurl'},
    'meeting': {'meeting', 'session', 'timeslot', 'room', 'floorplan', 'schedule',
                'schedtimesessassignment', 'sessionpresentation', 'registration', 'importantdate'},
}
EDITOR_MODELS = {f'{app}.{model}' for app, models in ADMIN_MODELS.items()
                 if app not in {'auth', 'name', 'person'} for model in models}
EDITOR_MODELS |= {'doc.documentauthor', 'doc.relateddocument', 'doc.documenturl'}
EDITOR_MODELS.discard('fgtida.outgoingmail')
EDITOR_MODELS.discard('fgtida.securityquestion')
EDITOR_MODELS.discard('fgtida.contributionrecipient')
ACCOUNT_VIEWS = {'index', 'confirm_new_email', 'create_account', 'confirm_account',
                 'change_password', 'profile', 'edit_person_externalresources',
                 'password_reset', 'confirm_password_reset', 'change_username', 'AnyEmailLoginView'}


class PublicationBoundaryMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if not request.path_info.endswith('/') and request.method in {'GET', 'HEAD'}:
            try:
                if resolve(request.path_info).namespace == 'fgtida':
                    return None
            except Resolver404:
                pass
            try:
                match = resolve(request.path_info + '/')
            except Resolver404:
                return None
            if match.namespace == 'fgtida':
                target = request.path + '/'
                if request.META.get('QUERY_STRING'):
                    target += '?' + request.META['QUERY_STRING']
                return HttpResponsePermanentRedirect(target)

    def process_view(self, request, view, args, kwargs):
        match = request.resolver_match
        if match.namespace == 'fgtida':
            return None
        module = getattr(view, '__module__', '')
        view_class = getattr(view, 'view_class', None)
        if module == 'ietf.person.views' and view.__name__ == 'ajax_select2_search':
            if request.user.is_staff and request.user.has_perm('person.view_person'):
                return None
            return HttpResponseNotFound('Not found')
        if module == 'ietf.ietfauth.views' and (getattr(view_class, '__name__', '') or view.__name__) in ACCOUNT_VIEWS:
            return None
        if view_class and view_class.__module__ == 'django.contrib.auth.views' and view_class.__name__ == 'LogoutView':
            return None
        if match.namespace == 'admin':
            if match.url_name == 'autocomplete':
                # Autocomplete has no model_admin attribute. Permit only
                # relationships between the explicitly exposed content models;
                # Django still enforces the target model's view permission.
                from django.apps import apps
                from django.core.exceptions import FieldDoesNotExist
                app, model = request.GET.get('app_label'), request.GET.get('model_name')
                if model not in ADMIN_MODELS.get(app, set()):
                    return HttpResponseNotFound('Not found')
                try:
                    field = apps.get_model(app, model)._meta.get_field(request.GET.get('field_name', ''))
                    target = field.remote_field.model._meta
                except (LookupError, FieldDoesNotExist, AttributeError):
                    return HttpResponseNotFound('Not found')
                if target.model_name not in ADMIN_MODELS.get(target.app_label, set()):
                    return HttpResponseNotFound('Not found')
                return None
            model_admin = getattr(view, 'model_admin', None)
            if model_admin:
                opts = model_admin.model._meta
                if opts.model_name in ADMIN_MODELS.get(opts.app_label, set()):
                    return None  # Django still enforces the model/object permissions.
            elif match.url_name in {'index', 'login', 'logout', 'password_change', 'password_change_done', 'jsi18n', 'app_list'}:
                return None
            return HttpResponseNotFound('Not found')
        if request.path_info == '/health/':
            return None
        if settings.DEBUG and module in {'django.views.static', 'django.contrib.staticfiles.views'}:
            return None
        return HttpResponseNotFound('Not found')
