"""Supervise one local shared cache and Gunicorn; fail closed on child failure."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time


def prepare_data_directories(data):
    data = Path(data)
    if not data.is_dir() or not os.access(data, os.W_OK):
        raise RuntimeError('The persistent /data mount must be writable by the tracker user.')
    root = data.resolve()
    # A mounted Render disk hides the empty directories in the image. Exports
    # contain files, so a directory with no uploads may not exist after restore.
    for relative in ('media/photo', 'proceedings', 'documents', 'charters',
                     'internet-drafts', 'rfc', 'static'):
        target = data / relative
        if not target.resolve().is_relative_to(root):
            raise RuntimeError('An application data directory points outside the persistent disk.')
        target.mkdir(mode=0o750, parents=True, exist_ok=True)


def main():
    if os.environ.get('FGTIDA_ENV') != 'production':
        raise RuntimeError('The Render service must use FGTIDA_ENV=production.')
    mode = os.environ.get('FGTIDA_RENDER_MODE', 'production')
    if mode not in {'bootstrap', 'staged', 'production'}:
        raise RuntimeError('FGTIDA_RENDER_MODE must be bootstrap, staged or production.')
    port = int(os.environ.get('PORT', '8000'))
    if not 1024 <= port <= 65535:
        raise RuntimeError('PORT must be an unprivileged TCP port.')
    data = Path(os.environ.get('FGTIDA_DATA_DIR', '/data'))
    prepare_data_directories(data)
    children = []

    def stop(signum, frame):
        for child in children:
            if child.poll() is None:
                child.terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if mode == 'bootstrap':
            # Health here means the maintenance server is up, not publication
            # readiness. It deliberately never imports Django or serves files.
            command = ['gunicorn', '--bind', f'0.0.0.0:{port}', '--workers', '1',
                       'deploy.render_bootstrap:application']
        else:
            children.append(subprocess.Popen(['memcached', '-l', '127.0.0.1', '-p', '11211',
                                               '-m', '64', '-U', '0']))
            for attempt in range(50):
                if children[0].poll() is not None:
                    raise RuntimeError('Local cache failed to start.')
                try:
                    with socket.create_connection(('127.0.0.1', 11211), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError('Local cache did not become ready.')
            check = [sys.executable, 'ietf/manage.py', 'check_fgtida_publication', '--skip-checks']
            if mode == 'staged':
                check.append('--allow-deferred-smtp')
            subprocess.run(check, check=True)
            subprocess.run([sys.executable, 'ietf/manage.py', 'collectstatic', '--noinput', '--skip-checks'], check=True)
            command = ['gunicorn', '--config', 'deploy/gunicorn.conf.py', 'ietf.wsgi:application']
        children.append(subprocess.Popen(command))
        if mode!='bootstrap' and os.environ.get('FGTIDA_GITHUB_SCHEDULE_ENABLED','').lower()=='true':
            # Runs beside Gunicorn with the same persistent disk; no separate
            # Cron container can safely write this disk-backed document catalog.
            children.append(subprocess.Popen([sys.executable,'deploy/maintenance.py']))
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        return next((child.returncode or 1 for child in children if child.poll() is not None), 1)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=35)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
