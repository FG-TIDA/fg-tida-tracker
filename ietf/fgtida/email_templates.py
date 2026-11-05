"""Plain-text transactional messages; delivery remains behind the ITU mail adapter."""


def greeting(user=None):
    name = (user.first_name if user else '') or ((user.person.name.split()[0] if hasattr(user, 'person') else '') if user else '')
    return f'Dear {name},' if name else 'Dear colleague,'


def confirmation(user, link):
    return ('FG-TIDA Datatracker — confirm your email address',
        f'{greeting(user)}\n\nWelcome to the FG-TIDA Datatracker. Please confirm your email address to activate your account:\n\n'
        f'{link}\n\nThis link expires in 24 hours. If you did not create this account, you can ignore this message.\n\nRegards,')


def password_reset(user, link):
    return ('FG-TIDA Datatracker — password reset request',
        f'{greeting(user)}\n\nWe received a request to reset your FG-TIDA Datatracker password. '
        f'Use the link below within one hour, complete the recovery checks and choose a new password:\n\n{link}\n\n'
        'If you did not request this change, ignore this message. Your password remains unchanged.\n\nRegards,')


def contribution(item, acknowledgement=False):
    subject = f'FG-TIDA — contribution received: {item.title}'.replace('\r',' ').replace('\n',' ')[:255]
    salutation = greeting(item.submitter) if acknowledgement else f'Dear {item.group.name} colleagues,'
    text = 'Your contribution has been received' if acknowledgement else 'A new contribution has been received'
    return (subject, f'{salutation}\n\n{text} and recorded in the FG-TIDA Datatracker.\n\n'
        f'Title: {item.title}\nGroup: {item.group.name}\nStatus: Submitted\n\n'
        f'{item.abstract}\n\nThe group will review the proposal and record its decision in the tracker.\n\nRegards,')
