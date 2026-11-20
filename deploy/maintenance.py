"""Optional single-instance Render scheduler sharing the application's /data disk."""
import subprocess
import os
import sys
import time


def invoke(command, timeout):
    started = time.monotonic()
    try:
        subprocess.run([sys.executable,'ietf/manage.py',*command,'--skip-checks'],
                       timeout=timeout,check=True)
    except (subprocess.SubprocessError, OSError):
        # Commands log bounded summaries; never echo environment or credentials.
        print('Scheduled maintenance command failed; retrying on the next cycle.',flush=True)
        return False
    finally:
        print(f'Maintenance {command[0]} duration={time.monotonic()-started:.1f}s', flush=True)
    return True


def main(task):
    last_catalog=0
    last_security_check=0
    last_archive_scan=None
    # Allow the web service to come up before the first maintenance operation.
    time.sleep({'mail': 60, 'github': 90, 'files': 120}[task])
    while True:
        if task == 'mail':
            invoke(['deliver_fgtida_mail'], 90)
        elif task == 'files':
            if not last_security_check or time.monotonic() - last_security_check >= 900:
                if not invoke(['scan_fgtida_files', '--check-scanner'], 180):
                    print('SECURITY: scanner self-test failed; file maintenance is held.', flush=True)
                    time.sleep(60)
                    continue
                last_security_check = time.monotonic()
            # Frequent retries are small. Do not reload the entire corpus each
            # minute; a full pass still checks unknown/due archives every 15 min.
            full = last_archive_scan is None or time.monotonic() - last_archive_scan >= 900
            command = ['scan_fgtida_files', '--limit', '20']
            if not full:
                command.append('--pending-only')
            invoke(command, 900 if full else 180)
            if full:
                last_archive_scan = time.monotonic()
        else:
            invoke(['process_fgtida_github', '--limit', '10'], 180)
            if time.monotonic() - last_catalog >= 900:
                invoke(['sync_fgtida_github', '--discover', '--apply'], 600)
                last_catalog = time.monotonic()
        time.sleep(60)


if __name__=='__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in {'mail', 'github', 'files'}:
        raise SystemExit('Choose mail, github or files maintenance.')
    try:
        os.nice(10)  # Web requests take precedence; children inherit priority.
    except (AttributeError, OSError):
        pass
    main(sys.argv[1])
