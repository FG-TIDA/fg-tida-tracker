"""Optional single-instance Render scheduler sharing the application's /data disk."""
import subprocess
import sys
import time


def invoke(command, timeout):
    try:
        subprocess.run([sys.executable,'ietf/manage.py',*command,'--skip-checks'],
                       timeout=timeout,check=True)
    except (subprocess.SubprocessError, OSError):
        # Commands log bounded summaries; never echo environment or credentials.
        print('Scheduled maintenance command failed; retrying on the next cycle.',flush=True)


def main(task):
    last_catalog=0
    # Allow the web service to come up before the first maintenance operation.
    time.sleep(60)
    while True:
        if task == 'mail':
            invoke(['deliver_fgtida_mail'], 90)
        else:
            invoke(['process_fgtida_github', '--limit', '10'], 180)
            if time.monotonic() - last_catalog >= 900:
                invoke(['sync_fgtida_github', '--discover', '--apply'], 600)
                last_catalog = time.monotonic()
        time.sleep(60)


if __name__=='__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in {'mail', 'github'}:
        raise SystemExit('Choose mail or github maintenance.')
    main(sys.argv[1])
