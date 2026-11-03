from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone
from .models import OutgoingMail


def enqueue(recipient, subject, body, expires=None, contribution=None):
    item = OutgoingMail.objects.create(recipient=recipient, subject=subject, body=body, expires=expires, contribution=contribution)
    if getattr(settings, 'FGTIDA_MAIL_IMMEDIATE', True):
        transaction.on_commit(lambda: deliver(item.pk))
    return item


@transaction.atomic
def deliver(pk):
    if not getattr(settings, 'FGTIDA_MAIL_ENABLED', False):
        return False
    item = OutgoingMail.objects.select_for_update().get(pk=pk)
    if item.sent or (item.expires and item.expires <= timezone.now()):
        return False
    if item.contribution_id and not item.contribution.group.contribution_recipients.filter(
            active=True, email__iexact=item.recipient).exists():
        item.expires = timezone.now()
        item.last_error = 'Recipient is no longer configured; notification cancelled.'
        item.save(update_fields=['expires', 'last_error'])
        return False
    item.attempts += 1
    try:
        message = EmailMessage(item.subject, item.body, settings.DEFAULT_FROM_EMAIL, [item.recipient])
        if item.contribution_id:
            import mimetypes
            attachment = item.contribution
            message.attach(attachment.filename, bytes(attachment.content),
                           mimetypes.guess_type(attachment.filename)[0] or 'application/octet-stream')
        if message.send() != 1:
            raise RuntimeError('Mail backend did not accept the message')
    except Exception:
        item.last_error = 'Delivery failed; check the SMTP configuration and retry.'
    else:
        item.sent = timezone.now()
        item.last_error = ''
    item.save(update_fields=['sent', 'attempts', 'last_error'])
    return bool(item.sent)
