import os

LOGIN_REDIRECT_URL = '/'
FGTIDA_REQUIRE_ADMIN_MFA = True
# Local development only. The policy helper also checks DEBUG and SERVER_MODE,
# so a copied development setting cannot bypass MFA in production.
FGTIDA_DEV_ADMIN_MFA_PAUSED = (os.environ.get('FGTIDA_ENV') != 'production'
    and os.environ.get('FGTIDA_DEV_ADMIN_MFA_PAUSED', 'true').lower() == 'true')
OTP_TOTP_ISSUER = 'FG-TIDA Datatracker'
FGTIDA_GITHUB_APP_ID = os.environ.get('FGTIDA_GITHUB_APP_ID', '')
FGTIDA_GITHUB_PRIVATE_KEY_FILE = os.environ.get('FGTIDA_GITHUB_PRIVATE_KEY_FILE', '')
FGTIDA_GITHUB_WRITE_ENABLED = os.environ.get('FGTIDA_GITHUB_WRITE_ENABLED', '').lower() == 'true'
FGTIDA_GITHUB_INGEST_ENABLED = os.environ.get('FGTIDA_GITHUB_INGEST_ENABLED', '').lower() == 'true'
_github_hook_file = os.environ.get('FGTIDA_GITHUB_WEBHOOK_SECRET_FILE', '')
if _github_hook_file:
    from pathlib import Path
    FGTIDA_GITHUB_WEBHOOK_SECRET = Path(_github_hook_file).read_text().strip()
else:
    FGTIDA_GITHUB_WEBHOOK_SECRET = ''

FGTIDA_MAIL_ENABLED = os.environ.get('FGTIDA_MAIL_ENABLED', '').lower() == 'true'
DEFAULT_FROM_EMAIL = os.environ.get('FGTIDA_FROM_EMAIL', 'FG-TIDA Secretariat <tsbfgtida@itu.int>')
SECRETARIAT_SUPPORT_EMAIL = 'tsbfgtida@itu.int'
