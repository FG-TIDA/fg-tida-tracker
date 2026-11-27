"""Revision access follows a verified account, never an unverified email string."""
from django.db.models import Q
from .models import Contribution


def verified_address(user):
    if not user or not user.is_authenticated or not user.is_active or not user.email:
        return ''
    from .accounts import verified_account_for_email
    owner = verified_account_for_email(user.email)
    return user.email.strip() if owner and owner.pk == user.pk else ''


def owned(user):
    if not user or not user.is_authenticated or not user.is_active:
        return Contribution.objects.none()
    condition = Q(submitter=user)
    if address := verified_address(user):
        condition |= Q(contact_email__iexact=address)
    return Contribution.objects.filter(condition, duplicate_of__isnull=True)


def is_owner(item, user):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if item.submitter_id == user.pk:
        return True
    address = verified_address(user)
    return bool(address and item.contact_email.strip().casefold() == address.casefold())


def registration_contact(address):
    return Contribution.objects.filter(contact_email__iexact=address).exists()
