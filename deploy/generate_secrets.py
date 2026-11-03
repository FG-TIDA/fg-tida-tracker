"""Create new local production secrets without displaying or overwriting them."""
import os
from pathlib import Path
import secrets


def main():
    directory = Path(__file__).resolve().parent / 'secrets'
    directory.mkdir(mode=0o700, exist_ok=True)
    created = 0
    for name in ('django_key', 'db_password', 'db_owner_password'):
        path = directory / name
        try:
            with path.open('x', encoding='utf-8') as target:
                os.chmod(path, 0o600)
                target.write(secrets.token_urlsafe(64))
            created += 1
        except FileExistsError:
            pass
    print(f'Created {created} private secret files; existing files were retained. No values were displayed.')
    print('Restrict directory access with your host ACL/secret manager. No SMTP credentials were generated.')


if __name__ == '__main__':
    main()
