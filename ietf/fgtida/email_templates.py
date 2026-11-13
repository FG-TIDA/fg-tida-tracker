"""FG-TIDA transactional email content."""
from django.template.loader import render_to_string


def greeting(user=None):
    name = (user.first_name if user else '') or ((user.person.name.split()[0] if hasattr(user, 'person') else '') if user else '')
    return f'Dear {name},' if name else 'Dear colleague,'


def confirmation(user, link, submit_url, tracker_meetings_url):
    greeting_text = greeting(user)
    intro = 'Thank you for your interest in contributing to the ITU-T Focus Group on Trust and Identity for Humans and Agentic AI (FG-TIDA). We share updates and ways to participate on our official FG-TIDA page and public GitHub organization.'
    activation = 'Confirm your email address to activate your Datatracker account. The confirmation link expires in 24 hours. If you did not create this account, you can ignore this message.'
    contribution = ('After activating your account, sign in to Datatracker and go to Documents → Submit a contribution. '
                    'Choose the working group relevant to your proposal. Select No working group yet if you are proposing work that could require a new working group. '
                    'Submit a GitHub repository at a fixed commit or an RST source ZIP. Word (.docx) is an exceptional fallback with automatic RST conversion and contributor verification. '
                    'Contributions from all sectors are welcome. You do not need to belong to a working group to submit.')
    minutes = 'Please check the meetings repository for minutes and slides within a week of each meeting. We keep updates there so you can follow them without a separate notification for each publication.'
    tracker_minutes = 'You can also find minutes and slides under Materials on each meeting’s Datatracker page.'
    links = [
        ('FG-TIDA on GitHub', 'https://github.com/FG-TIDA'),
        ('ITU GitLab mirror', 'https://www.itu.int/itad/gitlab/fg/fg-tida'),
        ('Terms of Reference', 'https://www.itu.int/en/ITU-T/focusgroups/tida/Pages/ToR.aspx'),
        ('Join fgtida@lists.itu.int', 'https://www.itu.int/en/ITU-T/focusgroups/tida/Pages/reg.aspx'),
        ('TIDA Community', 'https://tida.community/'),
        ('Official ITU-T FG-TIDA page', 'https://www.itu.int/en/ITU-T/focusgroups/tida/Pages/default.aspx'),
    ]
    workflow_url = 'https://fg-tida.github.io/.github/'
    meetings_url = 'https://github.com/FG-TIDA/meetings'
    access = 'To request access to the ITU GitLab mirror, contact tsbfgtida@itu.int.'
    closing = 'Thank you for collaborating with us.'
    no_reply = 'Please do not reply to this email.'
    body = '\n\n'.join([
        greeting_text, intro, f'{activation}\n{link}',
        f'Instructions on how to contribute\nSee the FG-TIDA contribution workflow for instructions: {workflow_url}',
        f'Contribute through Datatracker\n{contribution}\n{submit_url}',
        f'Meeting minutes and slides\n{minutes}\n{meetings_url}\n{tracker_minutes}\n{tracker_meetings_url}',
        'Useful links\n' + '\n'.join(f'{label}: {url}' for label, url in links),
        access, closing, 'FG-TIDA Secretariat,', no_reply,
    ])
    html = render_to_string('fgtida/email_confirmation.html', {
        'greeting': greeting_text, 'intro': intro, 'activation': activation, 'link': link,
        'workflow_url': workflow_url, 'contribution': contribution,
        'submit_url': submit_url, 'minutes': minutes, 'meetings_url': meetings_url,
        'tracker_minutes': tracker_minutes, 'tracker_meetings_url': tracker_meetings_url,
        'links': links, 'access': access, 'closing': closing, 'no_reply': no_reply,
    })
    return 'Welcome - email confirmation', body, html


def password_reset(user, link):
    return ('FG-TIDA Datatracker — password reset request',
        f'{greeting(user)}\n\nWe received a request to reset your FG-TIDA Datatracker password. '
        f'Use the link below within one hour, complete the recovery checks and choose a new password:\n\n{link}\n\n'
        'If you did not request this change, ignore this message. Your password remains unchanged.\n\nRegards,')


def contribution(item, acknowledgement=False):
    subject = f'FG-TIDA — contribution received: {item.title}'.replace('\r',' ').replace('\n',' ')[:255]
    salutation = greeting(item.submitter) if acknowledgement else f'Dear {item.group.name} colleagues,'
    text = 'Your contribution has been received' if acknowledgement else 'A new contribution has been received'
    from .accounts import mail_origin
    from django.urls import reverse
    details = mail_origin(None).rstrip('/') + reverse('fgtida:contribution', args=[item.pk])
    public_record = (mail_origin(None).rstrip('/') + reverse('fgtida:document', args=[item.document.name])) if item.document_id else ''
    source_note = ('Word was automatically converted to RST. The contributor must verify the conversion before adoption.\n'
                   if item.submission_method == 'word' else '')
    return (subject, f'{salutation}\n\n{text} and recorded in the FG-TIDA Datatracker.\n\n'
        f'Title: {item.title}\nGroup: {item.group.name}\nStatus: Submitted\n\n'
        f'{item.abstract}\n\nSubmission method: {item.get_submission_method_display()}\n{source_note}'
        f'Contribution and source preview (sign in): {details}\n\n'
        f'Public work item record: {public_record}\n\n'
        'The group will review the proposal and record its decision in the tracker.\n\nRegards,')
