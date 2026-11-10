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
from django.utils.crypto import salted_hmac, constant_time_compare
from django.views.decorators.debug import sensitive_post_parameters
from ietf.person.models import Email, Person
from .models import RecoveryQuestions, OutgoingMail, SecurityQuestion, EmailVerification
from .mail import enqueue

QUESTIONS = [('book', 'What was the title of a memorable childhood book?'),
    ('place', 'What place is associated with a personal memory you have not shared publicly?'),
    ('teacher', 'What nickname did you privately give a memorable teacher?'),
    ('project', 'What was the name of your first personal project?'),
    ('phrase', 'What private phrase will you remember for account recovery?'),
    ('object', 'What was a meaningful object from your childhood?')]
CUSTOM_QUESTION = ':custom:'
ACCOUNT_EMAIL_ORIGINS = {'FG-TIDA registration', 'FG-TIDA administration'}


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
        self.question_prompts = dict(question_choices())
        choices = [('', 'Choose a question'), *self.question_prompts.items(),
                   (CUSTOM_QUESTION, 'Write my own question')]
        for n in range(1, 4):
            self.fields[f'question_{n}'] = forms.ChoiceField(choices=choices, label=f'Security question {n}')
            self.fields[f'custom_question_{n}'] = forms.CharField(required=False, max_length=255,
                label=f'Your own question {n}', help_text='Fill this in only if you chose “Write my own question”.')
            self.fields[f'answer_{n}'] = forms.CharField(min_length=4, max_length=200, widget=forms.PasswordInput)

    def clean(self):
        values = super().clean()
        prompts = {}
        for n in range(1, 4):
            key = values.get(f'question_{n}')
            if key == CUSTOM_QUESTION:
                prompt = values.get(f'custom_question_{n}')
                if not prompt:
                    self.add_error(f'custom_question_{n}', 'Enter your own question.')
                    continue
                prompts[f'{CUSTOM_QUESTION}{n}'] = prompt
            elif key in self.question_prompts:
                prompts[key] = self.question_prompts[key]
        answers = [normalize(values.get(f'answer_{n}', '')) for n in range(1, 4)]
        if len({normalize(prompt) for prompt in prompts.values()}) != 3 or len(set(answers)) != 3 or any(len(a) < 4 for a in answers):
            raise forms.ValidationError('Choose three different questions and three different answers of at least four characters.')
        for prompt, answer in zip(prompts.values(), answers):
            if answer in normalize(prompt):
                raise forms.ValidationError('Keep your answers out of your security questions.')
        values['recovery_prompts'] = prompts
        return values

    def save(self, user):
        prompts = self.cleaned_data['recovery_prompts']
        RecoveryQuestions.objects.update_or_create(user=user, defaults={
            'questions': list(prompts),
            'prompts': prompts,
            'answer_hashes': [make_password(normalize(self.cleaned_data[f'answer_{n}'])) for n in range(1, 4)],
            'failures': 0, 'locked_until': None})


class SignupForm(QuestionsForm):
    name = forms.CharField(max_length=100)
    first_name = forms.CharField(required=False, max_length=100, label='Given name', help_text='Optional name used to address you in account emails.')
    biography = forms.CharField(required=False, max_length=5000, widget=forms.Textarea, help_text='Optional public biography.')
    password = forms.CharField(strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    password_confirmation = forms.CharField(strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def __init__(self, *args, email='', **kwargs):
        super().__init__(*args, **kwargs)
        self.email = email
        self.fields['password'].help_text = password_validation.password_validators_help_text_html()

    def clean(self):
        values = super().clean()
        if values.get('password') != values.get('password_confirmation'):
            self.add_error('password_confirmation', 'Passwords must match.')
        if values.get('password'):
            user = get_user_model()(username=self.email, email=self.email, first_name=values.get('first_name', ''))
            try:
                password_validation.validate_password(values['password'], user)
            except forms.ValidationError as error:
                self.add_error('password', error)
        return values


class EmailForm(forms.Form):
    email = forms.EmailField()


class SignupEmailForm(EmailForm):
    def clean_email(self):
        address = self.cleaned_data['email'].lower()
        max_length = get_user_model()._meta.get_field('username').max_length
        if len(address) > max_length:
            raise forms.ValidationError(f'Use an email address with at most {max_length} characters.')
        return address


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


def is_retired_account_email(email):
    return (email.origin in ACCOUNT_EMAIL_ORIGINS and email.person_id is not None
            and email.person.user_id is None)


def address_has_account(address):
    return get_user_model().objects.filter(Q(email__iexact=address) | Q(username__iexact=address)).exists()


def verified_account_for_email(address):
    # An address reused after deletion still points at the old Person in the
    # native catalogue. EmailVerification identifies the new account instead.
    try:
        verification = EmailVerification.objects.select_related('user').get(
            email__iexact=address, verified__isnull=False,
            user__is_active=True, user__email__iexact=address)
    except (EmailVerification.DoesNotExist, EmailVerification.MultipleObjectsReturned):
        return None
    return verification.user


def create(request):
    form = SignupEmailForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        address = form.cleaned_data['email']
        email = Email.objects.filter(address__iexact=address).select_related('person').first()
        if (email and not is_retired_account_email(email)) or address_has_account(address):
            form.add_error('email', 'This address is already registered or reserved. Sign in, reset your password, or contact the secretariat.')
        else:
            token = signing.dumps(address, salt='fgtida-signup')
            request.session['fgtida_signup'] = token
            return redirect('fgtida:signup-confirm', auth=token)
    return page(request, 'Create an account', form, 'Enter your email, complete your profile and confirm your email address to activate your account.')


@sensitive_post_parameters()
def confirm(request, auth):
    if request.session.get('fgtida_signup') != auth:
        return redirect('fgtida:signup')
    try:
        address = signing.loads(auth, salt='fgtida-signup', max_age=86400)
    except signing.BadSignature:
        raise Http404('Invalid or expired link')
    email_form = SignupEmailForm({'email': address})
    if not email_form.is_valid():
        return redirect('fgtida:signup')
    address = email_form.cleaned_data['email']
    form = SignupForm(request.POST or None, email=address)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            email = Email.objects.select_for_update(of=('self',)).filter(address__iexact=address).select_related('person').first()
            if (email and not is_retired_account_email(email)) or address_has_account(address):
                return page(request, 'Create an account', message='This email is already registered or reserved. Contact the secretariat if you need assistance.')
            # Unique username prevents simultaneous confirmation of one address.
            user, created = get_user_model().objects.get_or_create(username=address, defaults={'email': address})
            if not created:
                return redirect('ietf.ietfauth.views.login')
            user.set_password(form.cleaned_data['password'])
            user.is_active = False
            user.first_name = form.cleaned_data.get('first_name', '').strip()
            user.save()
            from unidecode import unidecode
            name = form.cleaned_data['name']
            person = Person.objects.create(user=user, name=name,
                ascii=unidecode(name)[:Person._meta.get_field('ascii').max_length],
                biography=form.cleaned_data['biography'])
            # Retain the old Email-to-Person relationship, including its use by
            # authors and group roles. The new account gets its own Person.
            if email is None:
                _, email_created = Email.objects.get_or_create(address=address,
                    defaults={'person': person, 'origin': 'FG-TIDA registration', 'active': False})
                if not email_created:
                    response = page(request, 'Create an account', message='This email is already registered or reserved. Contact the secretariat if you need assistance.')
                    transaction.set_rollback(True)
                    return response
            form.save(user)
            verification = EmailVerification.objects.create(user=user, email=address)
            queue_confirmation(request, verification)
            request.session.pop('fgtida_signup', None)
        return page(request, 'Confirm your email address', message=(
            'Your account has been created. Use the confirmation link sent to your email to activate it.'
            if getattr(settings, 'FGTIDA_MAIL_ENABLED', False) else
            'Your account has been created and is awaiting email confirmation. Email delivery is not enabled yet; contact tsbfgtida@itu.int for assistance.'))
    return page(request, 'Finish creating your account', form, 'Use answers that others cannot easily guess. Answers are stored as hashes.', 'Create account')


def email_proof(user, address):
    return salted_hmac('fgtida-email-confirmation', f'{user.pk}:{address}:{user.password}', algorithm='sha256').hexdigest()


def queue_confirmation(request, verification):
    user = verification.user
    token = signing.dumps([user.pk, verification.email, email_proof(user, verification.email)], salt='fgtida-email-confirmation')
    origin = mail_origin(request)
    link = origin + reverse('fgtida:email-confirm', args=[token])
    from .email_templates import confirmation
    subject, body, html = confirmation(user, link, origin + reverse('fgtida:submit'), origin + reverse('fgtida:meetings'))
    enqueue(verification.email, subject, body, timezone.now()+timedelta(hours=24), purpose='verification', html_body=html)


@never_cache
def email_confirm(request, auth):
    try:
        pk, address, token = signing.loads(auth, salt='fgtida-email-confirmation', max_age=86400)
        verification = EmailVerification.objects.select_related('user').get(user_id=pk, email=address)
    except (signing.BadSignature, EmailVerification.DoesNotExist, ValueError, TypeError):
        raise Http404('Invalid or expired confirmation link')
    if verification.verified:
        return page(request, 'Email already confirmed', message='Your email address is already confirmed. You can sign in.')
    if not constant_time_compare(email_proof(verification.user, address), token) or verification.user.email.casefold() != address.casefold():
        raise Http404('Invalid or expired confirmation link')
    if request.method == 'POST':
        with transaction.atomic():
            verification = EmailVerification.objects.select_for_update().select_related('user').get(pk=verification.pk)
            user = get_user_model().objects.select_for_update().get(pk=pk)
            if not verification.verified:
                if not constant_time_compare(email_proof(user, address), token) or user.email.casefold() != address.casefold():
                    raise Http404('Invalid or expired confirmation link')
                user.is_active = True
                user.save(update_fields=['is_active'])
                Email.objects.filter(person__user=user, address__iexact=address).update(active=True)
                verification.verified = timezone.now()
                verification.save(update_fields=['verified'])
        return page(request, 'Email confirmed', message='Your account is active. You can now sign in.')
    return page(request, 'Confirm your email address', forms.Form(), 'Confirm this email address to activate your FG-TIDA Datatracker account.', 'Confirm email')


@never_cache
def resend_confirmation(request):
    form = EmailForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        address = form.cleaned_data['email'].lower()
        verification = EmailVerification.objects.filter(email__iexact=address, verified__isnull=True).select_related('user').first()
        if verification and allowed_request(address):
            queue_confirmation(request, verification)
        return page(request, 'Email confirmation', message='If this account is awaiting confirmation, a new link will be emailed. Delivery remains deferred until the ITU email service is configured.' if not getattr(settings, 'FGTIDA_MAIL_ENABLED', False) else 'If this account is awaiting confirmation, a new link will be emailed.')
    return page(request, 'Resend email confirmation', form)


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
            from .email_templates import password_reset
            subject, body = password_reset(user, link)
            enqueue(recipient, subject, body, timezone.now()+timedelta(hours=1), purpose='reset')
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
