from datetime import timedelta
import unicodedata
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from ietf.person.models import Email
from . import core
from .models import RecoveryQuestions, OutgoingMail, SecurityQuestion
from .mail import enqueue

QUESTIONS = [('book', 'What was the title of a memorable childhood book?'),
    ('place', 'What place is associated with a personal memory you have not shared publicly?'),
    ('teacher', 'What nickname did you privately give a memorable teacher?'),
    ('project', 'What was the name of your first personal project?'),
    ('phrase', 'What private phrase will you remember for account recovery?'),
    ('object', 'What was a meaningful object from your childhood?')]


def question_choices():
    return list(SecurityQuestion.objects.filter(active=True).values_list('slug', 'prompt')) or QUESTIONS


def question_label(recovery, slug):
    return recovery.prompts.get(slug) or dict(SecurityQuestion.objects.values_list('slug', 'prompt')).get(slug) or dict(QUESTIONS).get(slug, 'Security answer')


@require_GET
@never_cache
def csrf_token(request):
    """Refresh a same-origin form token after another tab rotates the cookie."""
    return JsonResponse({'csrfToken': get_token(request)})


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


class QuestionsForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for n in range(1, 4):
            self.fields[f'question_{n}'] = forms.ChoiceField(choices=[('', 'Choose a question')] + question_choices())
            self.fields[f'answer_{n}'] = forms.CharField(min_length=4, max_length=200, widget=forms.PasswordInput)

    def clean(self):
        values = super().clean()
        questions = [values.get(f'question_{n}') for n in range(1, 4)]
        answers = [normalize(values.get(f'answer_{n}', '')) for n in range(1, 4)]
        if len(set(questions)) != 3 or len(set(answers)) != 3 or any(len(a) < 4 for a in answers):
            raise forms.ValidationError('Choose three different questions and three different answers of at least four characters.')
        return values

    def save(self, user):
        RecoveryQuestions.objects.update_or_create(user=user, defaults={
            'questions': [self.cleaned_data[f'question_{n}'] for n in range(1, 4)],
            'prompts': {self.cleaned_data[f'question_{n}']: dict(question_choices())[self.cleaned_data[f'question_{n}']] for n in range(1, 4)},
            'answer_hashes': [make_password(normalize(self.cleaned_data[f'answer_{n}'])) for n in range(1, 4)],
            'failures': 0, 'locked_until': None})


class SignupForm(QuestionsForm):
    name = forms.CharField(max_length=100)
    biography = forms.CharField(required=False, max_length=5000, widget=forms.Textarea, help_text='Optional public biography.')
    password = forms.CharField(widget=forms.PasswordInput)
    password_confirmation = forms.CharField(widget=forms.PasswordInput)

    def clean(self):
        values = super().clean()
        if values.get('password') != values.get('password_confirmation'):
            self.add_error('password_confirmation', 'Passwords must match.')
        if values.get('password'):
            password_validation.validate_password(values['password'])
        return values


class EmailForm(forms.Form):
    email = forms.EmailField()


def page(request, title, form=None, message='', button='Continue'):
    response = render(request, 'fgtida/account_form.html', {'title': title, 'form': form, 'message': message, 'button': button})
    response['Cache-Control'] = 'no-store'
    # no-referrer can make browsers send Origin: null on POST, which Django
    # correctly rejects. Keep same-origin forms working without leaking signed
    # recovery URLs to external destinations.
    response['Referrer-Policy'] = 'same-origin'
    return response


def mail_origin(request):
    # Use configured origin/site, never an untrusted Host header in recovery links.
    from django.contrib.sites.models import Site
    return getattr(settings, 'SITE_URL', '') if getattr(settings, 'FGTIDA_PUBLIC_DOMAIN', '') else ('http://' if settings.DEBUG else 'https://') + Site.objects.get_current().domain


def allowed_request(email):
    # Shared PostgreSQL limit across all workers; no account existence disclosure.
    return OutgoingMail.objects.filter(recipient=email, created__gte=timezone.now()-timedelta(hours=1)).count() < 5


def create(request):
    form = EmailForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        address = form.cleaned_data['email'].lower()
        if Email.objects.filter(address__iexact=address).exists() or get_user_model().objects.filter(Q(email__iexact=address) | Q(username__iexact=address)).exists():
            form.add_error('email', 'This address is already registered or reserved. Sign in, reset your password, or contact the secretariat.')
        else:
            token = signing.dumps(address, salt='fgtida-signup')
            request.session['fgtida_signup'] = token
            return redirect('fgtida:signup-confirm', auth=token)
    return page(request, 'Create an account', form, 'Enter your email to continue to your profile, password and three security questions. No confirmation email is required.')


@sensitive_post_parameters()
def confirm(request, auth):
    if request.session.get('fgtida_signup') != auth:
        return redirect('fgtida:signup')
    try:
        address = signing.loads(auth, salt='fgtida-signup', max_age=86400)
    except signing.BadSignature:
        raise Http404('Invalid or expired link')
    form = SignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            existing_email = Email.objects.select_for_update(of=('self',)).filter(address=address).select_related('person').first()
            if existing_email:
                return page(request, 'Create an account', message='This email is already registered or reserved. Contact the secretariat if you need assistance.')
            # Unique username prevents simultaneous confirmation of one address.
            user, created = get_user_model().objects.get_or_create(username=address, defaults={'email': address})
            if not created:
                return redirect('ietf.ietfauth.views.login')
            user.set_password(form.cleaned_data['password'])
            user.save()
            person = existing_email.person if existing_email and existing_email.person_id else core.actor_for(user)
            person.user = user
            person.name = form.cleaned_data['name']
            person.biography = form.cleaned_data['biography']
            from unidecode import unidecode
            person.ascii = unidecode(person.name)
            person.save()
            email, _ = Email.objects.get_or_create(address=address, defaults={'person': person, 'origin': 'FG-TIDA registration'})
            if email.person_id and email.person_id != person.pk:
                raise forms.ValidationError('Email is already assigned; contact the secretariat.')
            email.person = person
            email.active = True
            email.save()
            form.save(user)
            enqueue(address, 'Welcome to FG-TIDA', 'Welcome to the FG-TIDA Datatracker. Your account is ready. You can sign in and submit contributions. Contact tsbfgtida@itu.int for assistance.')
            request.session.pop('fgtida_signup', None)
        return redirect('ietf.ietfauth.views.login')
    return page(request, 'Finish creating your account', form, 'Use answers that others cannot easily guess. Answers are stored as hashes.', 'Create account')


@login_required
@sensitive_post_parameters()
def security(request):
    form = QuestionsForm(request.POST or None)
    form.fields['current_password'] = forms.CharField(widget=forms.PasswordInput)
    existing = RecoveryQuestions.objects.filter(user=request.user).first()
    if existing:
        from .account_changes import add_answers
        add_answers(form, existing, prefix='existing_answer_')
    if request.method == 'POST' and form.is_valid():
        if not request.user.check_password(form.cleaned_data['current_password']):
            form.add_error('current_password', 'Incorrect password.')
        else:
            if existing:
                from .account_changes import verify_answers
                error = verify_answers(request.user, form.cleaned_data, prefix='existing_answer_')
                if error:
                    form.add_error(None, error)
                    return page(request, 'Recovery questions', form, 'Confirm your current password and existing answers before replacing your questions.', 'Save questions')
            form.save(request.user)
            return page(request, 'Recovery questions', message='Your three recovery questions have been saved.')
    return page(request, 'Recovery questions', form, 'Confirm your current password to set or replace your recovery questions.', 'Save questions')


def reset(request):
    form = EmailForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        address = form.cleaned_data['email'].lower()
        user = get_user_model().objects.filter(is_active=True).filter(Q(email__iexact=address) | Q(person__email__address__iexact=address, person__email__active=True)).distinct().first()
        if user and user.has_usable_password() and allowed_request(address):
            token = default_token_generator.make_token(user)
            auth = signing.dumps([user.pk, token], salt='fgtida-reset')
            link = mail_origin(request) + reverse('fgtida:reset-confirm', args=[auth])
            recipient = Email.objects.filter(person__user=user, address__iexact=address, active=True).values_list('address', flat=True).first() or user.email
            enqueue(recipient, 'Reset your FG-TIDA password', f'Open this link within one hour, answer your three recovery questions, and choose a new password:\n{link}\nIgnore this message if you did not request a reset.', timezone.now()+timedelta(hours=1))
        return page(request, 'Password reset', message='If the address matches an active account, reset instructions will be emailed. Email delivery is pending SMTP setup.' if not getattr(settings, 'FGTIDA_MAIL_ENABLED', False) else 'If the address matches an active account, reset instructions will be emailed.')
    return page(request, 'Password reset', form)


@sensitive_post_parameters()
def reset_confirm(request, auth):
    try:
        pk, token = signing.loads(auth, salt='fgtida-reset', max_age=3600)
        user = get_user_model().objects.get(pk=pk, is_active=True)
    except (signing.BadSignature, get_user_model().DoesNotExist, ValueError):
        raise Http404('Invalid or expired link')
    if not default_token_generator.check_token(user, token):
        raise Http404('Invalid or expired link')
    recovery = RecoveryQuestions.objects.filter(user=user).first()
    if not recovery:
        return page(request, 'Password reset', message='Recovery questions have not been set. Contact tsbfgtida@itu.int for account recovery.')
    from ietf.ietfauth.forms import PasswordForm
    form = PasswordForm(request.POST or None, user=user)
    for n, question in enumerate(recovery.questions, 1):
        form.fields[f'answer_{n}'] = forms.CharField(label=question_label(recovery, question), max_length=200, widget=forms.PasswordInput)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            recovery = RecoveryQuestions.objects.select_for_update().get(pk=recovery.pk)
            user = get_user_model().objects.select_for_update().get(pk=user.pk)
            if not default_token_generator.check_token(user, token):
                raise Http404('Expired reset link')
            if recovery.locked_until and recovery.locked_until > timezone.now():
                form.add_error(None, 'Too many attempts. Try again in 15 minutes.')
            else:
                valid = [check_password(normalize(form.cleaned_data[f'answer_{n}']), hashed) for n, hashed in enumerate(recovery.answer_hashes, 1)]
                if len(valid) != 3 or not all(valid):
                    recovery.failures += 1
                    if recovery.failures >= 5:
                        recovery.locked_until = timezone.now()+timedelta(minutes=15)
                        recovery.failures = 0
                    recovery.save()
                    form.add_error(None, 'The recovery answers did not match.')
                else:
                    user.set_password(form.cleaned_data['password'])
                    user.save()
                    recovery.failures = 0
                    recovery.locked_until = None
                    recovery.save()
                    return page(request, 'Password reset complete', message='Your password was changed. You can now sign in.')
    response = page(request, 'Confirm password reset', form, 'Answer all three questions and choose a new password.', 'Reset password')
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'same-origin'
    return response
