import os

LOGIN_REDIRECT_URL = '/'
FGTIDA_REQUIRE_ADMIN_MFA = True
# Local development only. The policy helper also checks DEBUG and SERVER_MODE,
# so a copied development setting cannot bypass MFA in production.
FGTIDA_DEV_ADMIN_MFA_PAUSED = (os.environ.get('FGTIDA_ENV') != 'production'
    and os.environ.get('FGTIDA_DEV_ADMIN_MFA_PAUSED', 'true').lower() == 'true')
OTP_TOTP_ISSUER = 'FG-TIDA Datatracker'

FGTIDA_MAIL_ENABLED = os.environ.get('FGTIDA_MAIL_ENABLED', '').lower() == 'true'
DEFAULT_FROM_EMAIL = os.environ.get('FGTIDA_FROM_EMAIL', 'FG-TIDA Secretariat <tsbfgtida@itu.int>')
SECRETARIAT_SUPPORT_EMAIL = 'tsbfgtida@itu.int'
