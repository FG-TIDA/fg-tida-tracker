"""Authenticated self-service changes with password and recovery-answer checks."""
from datetime import timedelta
from django import forms
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from unidecode import unidecode
from ietf.person.models import Email
from . import core
from .accounts import question_label, normalize, page
from .models import RecoveryQuestions


def add_answers(form, recovery, prefix='answer_'):
    for n, question in enumerate(recovery.questions, 1):
        form.fields[f'{prefix}{n}'] = forms.CharField(
            label=question_label(recovery, question),
            max_length=200, widget=forms.PasswordInput)


@transaction.atomic
def verify_answers(user, values, prefix='answer_'):
    recovery = RecoveryQuestions.objects.select_for_update().get(user=user)
    if recovery.locked_until and recovery.locked_until > timezone.now():
        return 'Too many attempts. Try again in 15 minutes.'
    valid = [check_password(normalize(values.get(f'{prefix}{n}', '')), hashed)
             for n, hashed in enumerate(recovery.answer_hashes, 1)]
    if len(valid) != 3 or not all(valid):
        recovery.failures += 1
        if recovery.failures >= 5:
            recovery.locked_until = timezone.now() + timedelta(minutes=15)
            recovery.failures = 0
        recovery.save()
        return 'The security answers did not match.'
    recovery.failures = 0
    recovery.locked_until = None
    recovery.save()
    return None


class ProfileForm(forms.Form):
    name = forms.CharField(max_length=100)
    biography = forms.CharField(required=False, max_length=5000, widget=forms.Textarea)
    current_password = forms.CharField(widget=forms.PasswordInput)


class UsernameForm(forms.Form):
    username = forms.CharField(max_length=150, validators=get_user_model()._meta.get_field('username').validators)
    current_password = forms.CharField(widget=forms.PasswordInput)


@login_required
@sensitive_post_parameters()
def change(request, kind='profile'):
    recovery = RecoveryQuestions.objects.filter(user=request.user).first()
    if not recovery:
        from django.shortcuts import redirect
        return redirect('fgtida:security')
    person = core.actor_for(request.user)
    title = {'profile': 'Account information', 'password': 'Change password', 'username': 'Change username'}[kind]
    if kind == 'password':
        form = PasswordChangeForm(request.user, request.POST or None)
    elif kind == 'username':
        form = UsernameForm(request.POST or None, initial={'username': request.user.username})
    else:
        form = ProfileForm(request.POST or None, initial={'name': person.name, 'biography': person.biography})
    add_answers(form, recovery)
    if request.method == 'POST' and form.is_valid():
        if kind != 'password' and not request.user.check_password(form.cleaned_data['current_password']):
            form.add_error('current_password', 'Incorrect password.')
        elif kind == 'username' and get_user_model().objects.exclude(pk=request.user.pk).filter(
            Q(username__iexact=form.cleaned_data['username']) | Q(email__iexact=form.cleaned_data['username'])
        ).exists():
            form.add_error('username', 'This username is unavailable.')
        else:
            error = verify_answers(request.user, form.cleaned_data)
            if error:
                form.add_error(None, error)
            else:
                if kind == 'password':
                    user = form.save()
                    update_session_auth_hash(request, user)
                elif kind == 'username':
                    request.user.username = form.cleaned_data['username']
                    request.user.save(update_fields=['username'])
                else:
                    person.name = form.cleaned_data['name']
                    person.ascii = unidecode(person.name)
                    person.biography = form.cleaned_data['biography']
                    person.save()
                return page(request, title, message='Your changes have been saved.')
    email = request.user.email or Email.objects.filter(person=person, active=True).values_list('address', flat=True).first() or ''
    return page(request, title, form, f'Account email: {email}. Confirm your current password and all three security answers to save changes. Contact the secretariat to change your email address.', 'Save changes')
