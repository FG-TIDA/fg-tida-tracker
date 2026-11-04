"""Authenticator-app MFA using django-otp; no secrets in URLs or logs."""
import base64
import secrets
from copy import copy
from datetime import timedelta
from urllib.parse import unquote, urlsplit
from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.http import HttpResponse
from django.http import QueryDict
from django.urls import resolve, Resolver404
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice
from .models import MfaRecoveryCode, AuthenticationAttempt


def digest(value):
    return salted_hmac('fgtida-mfa-recovery', value, algorithm='sha256').hexdigest()


@transaction.atomic
def allow_attempt(scope, limit=10):
    now = timezone.now()
    key = salted_hmac('fgtida-auth-throttle', scope, algorithm='sha256').hexdigest()
    row, _ = AuthenticationAttempt.objects.select_for_update().get_or_create(key=key,
        defaults={'expires': now + timedelta(minutes=15)})
    if row.expires <= now:
        row.failures = 0
        row.expires = now + timedelta(minutes=15)
    if row.failures >= limit:
        return False
    row.failures += 1
    row.save()
    return True


def admin_mfa_paused(user):
    return (user.is_authenticated and (user.is_staff or user.is_superuser)
        and settings.DEBUG and settings.SERVER_MODE == 'development'
        and getattr(settings, 'FGTIDA_DEV_ADMIN_MFA_PAUSED', False))


def required_for(user):
    if admin_mfa_paused(user):
        return False
    return getattr(settings, 'FGTIDA_REQUIRE_ADMIN_MFA', True) and (user.is_staff or user.is_superuser)


def available_destination(request, target):
    """Accept local, published destinations without executing their views."""
    if not isinstance(target, str) or not url_has_allowed_host_and_scheme(
            target, {request.get_host()}, require_https=not settings.DEBUG):
        return False
    parsed = urlsplit(target)
    path = unquote(parsed.path)
    if not path.startswith('/') or path.startswith(('/accounts/mfa/', settings.STATIC_URL)):
        return False
    if path in {'/accounts/login/', '/accounts/logout/', '/admin/login/', '/admin/logout/'}:
        return False
    try:
        match = resolve(path)
    except Resolver404:
        return False
    if match.namespace not in {'fgtida', 'admin'} and match.func.__module__ != 'ietf.ietfauth.views':
        return False
    if match.namespace == 'admin' and match.url_name in {'autocomplete', 'jsi18n'}:
        return False
    # A registered upstream URL can still be intentionally unavailable here.
    # Reuse the publication boundary instead of maintaining a second allowlist.
    from .security import ADMIN_MODELS, PublicationBoundaryMiddleware
    if match.namespace == 'admin' and match.url_name == 'app_list' and match.kwargs.get('app_label') not in ADMIN_MODELS:
        return False
    probe = copy(request)
    probe.path_info = path
    probe.path = parsed.path
    probe.GET = QueryDict(parsed.query)
    probe.resolver_match = match
    return PublicationBoundaryMiddleware(lambda request: None).process_view(
        probe, match.func, match.args, match.kwargs) is None


def page_navigation(request):
    """Background fetches, scripts and icons must not replace the login target."""
    if request.method != 'GET' or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return False
    destination = request.headers.get('Sec-Fetch-Dest')
    if destination is not None:
        return destination == 'document'
    accept = request.headers.get('Accept')
    return accept is None or 'text/html' in accept or 'application/xhtml+xml' in accept


class MfaGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        protected_posts = {'/accounts/login/', '/admin/login/', '/accounts/reset/', '/accounts/security/',
            '/accounts/profile/', '/accounts/password/', '/accounts/username/'}
        if request.method == 'POST' and request.path in protected_posts:
            identity = str(request.user.pk) if request.user.is_authenticated else request.POST.get('username', request.POST.get('email', '')).strip().casefold()[:254]
            if identity and not allow_attempt(f'account:{request.path}:{identity}', limit=20):
                response = HttpResponse('Too many attempts. Please try again in 15 minutes.', status=429)
                response['Retry-After'] = '900'
                response['Cache-Control'] = 'no-store'
                return response
        if request.user.is_authenticated and not admin_mfa_paused(request.user):
            paths = {'/accounts/mfa/setup/', '/accounts/mfa/verify/', '/accounts/logout/', '/admin/logout/', '/accounts/csrf/'}
            if request.path not in paths and not request.path.startswith(settings.STATIC_URL):
                device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
                if device or required_for(request.user):
                    if not request.user.is_verified():
                        if page_navigation(request) and available_destination(request, request.get_full_path()):
                            request.session['fgtida_mfa_next'] = request.get_full_path()
                        return redirect('fgtida:mfa-verify' if device else 'fgtida:mfa-setup')
        return self.get_response(request)


class VerifyForm(forms.Form):
    token = forms.CharField(label='Authenticator code or recovery code', max_length=64,
        widget=forms.TextInput(attrs={'autocomplete': 'one-time-code', 'autocapitalize': 'none'}))


class SetupForm(VerifyForm):
    current_password = forms.CharField(widget=forms.PasswordInput)


def page(request, **context):
    response = render(request, 'fgtida/mfa.html', context)
    response['Cache-Control'] = 'no-store, private'
    response['Referrer-Policy'] = 'same-origin'
    return response


def destination(request):
    target = request.session.pop('fgtida_mfa_next', '/')
    if not available_destination(request, target):
        return '/'
    return target


@transaction.atomic
def verify_code(user, token):
    device = TOTPDevice.objects.select_for_update().filter(user=user, confirmed=True).first()
    if not device:
        return None
    token = token.strip().replace(' ', '')
    if device.verify_token(token):
        return device
    code = MfaRecoveryCode.objects.select_for_update().filter(user=user, digest=digest(token.lower())).first()
    if code:
        code.delete()
        return device
    return None


def recovery_codes(user):
    codes = [secrets.token_hex(16) for _ in range(10)]
    MfaRecoveryCode.objects.filter(user=user).delete()
    MfaRecoveryCode.objects.bulk_create([MfaRecoveryCode(user=user, digest=digest(code)) for code in codes])
    return codes


@login_required
@never_cache
@sensitive_post_parameters()
@sensitive_variables()
def setup(request):
    if admin_mfa_paused(request.user):
        return redirect(destination(request))
    if TOTPDevice.objects.filter(user=request.user, confirmed=True).exists():
        return redirect('fgtida:mfa-verify' if not request.user.is_verified() else 'fgtida:mfa-settings')
    form = SetupForm(None if request.POST.get('begin') == '1' else request.POST or None)
    # GET is a confirmation screen; generating a seed requires a CSRF-protected POST.
    if request.method == 'GET':
        return page(request, title='Set up two-factor authentication', begin=True)
    if not allow_attempt(f'mfa-setup:{request.user.pk}', limit=20):
        return page(request, title='Two-factor authentication', message='Too many attempts. Try again in 15 minutes.')
    with transaction.atomic():
        # Lock the user so concurrent setup requests cannot confirm two seeds.
        from django.contrib.auth import get_user_model
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        if TOTPDevice.objects.filter(user=request.user, confirmed=True).exists():
            return redirect('fgtida:mfa-verify')
        device = TOTPDevice.objects.filter(user=request.user, confirmed=False, name='Authenticator').first()
        if not device:
            device = TOTPDevice.objects.create(user=request.user, confirmed=False, name='Authenticator')
        if request.POST.get('begin') != '1' and form.is_valid():
            if not request.user.check_password(form.cleaned_data['current_password']):
                form.add_error('current_password', 'Incorrect password.')
            elif not device.verify_token(form.cleaned_data['token'].strip()):
                form.add_error('token', 'Invalid or recently used code. Wait for the next code and try again.')
            else:
                device.confirmed = True
                device.save(update_fields=['confirmed'])
                otp_login(request, device)
                request.session.cycle_key()
                return page(request, title='Two-factor authentication enabled', codes=recovery_codes(request.user), continue_url=destination(request))
        seed = base64.b32encode(device.bin_key).decode().rstrip('=')
        return page(request, title='Set up two-factor authentication', form=form, seed=seed, account=request.user.username)


@login_required
@never_cache
@sensitive_post_parameters()
def verify(request):
    if admin_mfa_paused(request.user):
        return redirect(destination(request))
    if request.user.is_verified():
        return redirect(destination(request))
    if not TOTPDevice.objects.filter(user=request.user, confirmed=True).exists():
        return redirect('fgtida:mfa-setup' if required_for(request.user) else 'fgtida:home')
    form = VerifyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        if not allow_attempt(f'mfa-verify:{request.user.pk}'):
            form.add_error(None, 'Too many attempts. Try again in 15 minutes.')
        else:
            device = verify_code(request.user, form.cleaned_data['token'])
            if device:
                otp_login(request, device)
                request.session.cycle_key()
                return redirect(destination(request))
            form.add_error('token', 'Invalid or recently used code.')
    return page(request, title='Verify your sign-in', form=form)


@login_required
@never_cache
@sensitive_post_parameters()
def manage(request):
    device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
    if not device:
        return page(request, title='Two-factor authentication', begin=True)
    form = SetupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        if not allow_attempt(f'mfa-manage:{request.user.pk}'):
            form.add_error(None, 'Too many attempts. Try again in 15 minutes.')
        elif not request.user.check_password(form.cleaned_data['current_password']):
            form.add_error('current_password', 'Incorrect password.')
        elif not verify_code(request.user, form.cleaned_data['token']):
            form.add_error('token', 'Invalid or recently used code.')
        elif request.POST.get('action') == 'disable':
            if required_for(request.user):
                form.add_error(None, 'Administrators must keep two-factor authentication enabled.')
            else:
                with transaction.atomic():
                    TOTPDevice.objects.filter(user=request.user).delete()
                    MfaRecoveryCode.objects.filter(user=request.user).delete()
                request.session.pop('otp_device_id', None)
                return page(request, title='Two-factor authentication', message='Two-factor authentication has been disabled.')
        elif request.POST.get('action') == 'codes':
            with transaction.atomic():
                return page(request, title='New recovery codes', codes=recovery_codes(request.user), continue_url='/')
    return page(request, title='Two-factor authentication', form=form, managing=True, mandatory=required_for(request.user))
