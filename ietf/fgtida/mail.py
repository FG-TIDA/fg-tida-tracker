import smtplib

from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone
from .models import OutgoingMail


def enqueue(recipient, subject, body, expires=None, contribution=None, purpose=None, html_body=''):
    item = OutgoingMail.objects.create(recipient=recipient, subject=subject, body=body, expires=expires,
        contribution=contribution, purpose=purpose or ('group' if contribution else 'general'), html_body=html_body)
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
    if item.contribution_id and item.purpose != 'submitter' and not item.contribution.group.contribution_recipients.filter(
            active=True, email__iexact=item.recipient).exists():
        item.expires = timezone.now()
        item.last_error = 'Recipient is no longer configured; notification cancelled.'
        item.save(update_fields=['expires', 'last_error'])
        return False
    if item.contribution_id and item.purpose == 'submitter' and (
        not item.contribution.submitter_id or item.recipient.casefold() != item.contribution.submitter.email.casefold()
    ):
        item.expires = timezone.now()
        item.last_error = 'Submitter account was removed or email changed; notification cancelled.'
        item.save(update_fields=['expires', 'last_error'])
        return False
    item.attempts += 1
    try:
        message_type = EmailMultiAlternatives if item.html_body else EmailMessage
        message = message_type(item.subject, item.body, settings.DEFAULT_FROM_EMAIL, [item.recipient])
        if item.html_body:
            message.attach_alternative(item.html_body, 'text/html')
        if item.contribution_id:
            import mimetypes
            attachment = item.contribution
            message.attach(attachment.filename, bytes(attachment.content),
                           mimetypes.guess_type(attachment.filename)[0] or 'application/octet-stream')
        if message.send() != 1:
            raise RuntimeError('Mail backend did not accept the message')
    except smtplib.SMTPAuthenticationError as error:
        item.last_error = f'SMTP authentication failed ({error.smtp_code}). Check the sending credential.'
    except smtplib.SMTPRecipientsRefused:
        item.last_error = 'SMTP rejected the recipient. Check the address and mail provider.'
    except smtplib.SMTPResponseException as error:
        item.last_error = f'SMTP rejected the request ({error.smtp_code}). Check the sending domain, limits and provider logs.'
    except (OSError, smtplib.SMTPException):
        item.last_error = 'SMTP connection failed. Check the server, port and TLS settings.'
    except Exception:
        item.last_error = 'Delivery failed; check the SMTP configuration and retry.'
    else:
        item.sent = timezone.now()
        item.last_error = ''
    item.save(update_fields=['sent', 'attempts', 'last_error'])
    return bool(item.sent)
