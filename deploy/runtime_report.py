"""Small, read-only Linux runtime sample. Never print arguments or environment."""
import json
import os
from pathlib import Path
import time


JOBS = {'scan_fgtida_files', 'sync_fgtida_github', 'process_fgtida_github',
        'deliver_fgtida_mail', 'collectstatic', 'migrate', 'check_fgtida_publication'}
MODULES = {'rst_worker', 'extract_worker', 'safety_worker', 'word_worker'}


def label(arguments):
    args = arguments.split(b'\0')
    for index, argument in enumerate(args):
        name = argument.decode('utf-8', errors='replace').replace('\\', '/').rsplit('/', 1)[-1]
        if name == 'maintenance.py':
            task = args[index+1].decode('ascii', errors='ignore') if index+1 < len(args) else ''
            return 'maintenance:' + task if task in {'files', 'github', 'mail'} else 'maintenance'
        if name in JOBS:
            return 'job:' + name
        if argument.startswith(b'ietf.fgtida.') and name.rsplit('.', 1)[-1] in MODULES:
            return 'parser:' + name.rsplit('.', 1)[-1]
        if name == 'gunicorn' or argument.startswith(b'gunicorn:'):
            return 'web:gunicorn'
        if name in {'memcached', 'nginx'}:
            return name
    return 'other'  # Never expose arbitrary argv[0], file paths or tokens.


def snapshot():
    result = {}
    page = os.sysconf('SC_PAGE_SIZE')
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path/'stat').read_text().rpartition(')')[2].split()
            result[int(path.name)] = {'ticks': int(fields[11])+int(fields[12]),
                'start': fields[19], 'ppid': int(fields[1]), 'nice': int(fields[16]),
                'rss_mb': round(int(fields[21])*page/1024**2, 1),
                'task': label((path/'cmdline').read_bytes()[:16384])}
        except (OSError, ValueError, IndexError):
            continue
    return result


def cgroup_stats():
    result = {}
    for name in ('memory.current', 'memory.max', 'cpu.max', 'cpu.stat'):
        try:
            result[name] = (Path('/sys/fs/cgroup')/name).read_text().strip()
        except OSError:
            result[name] = 'unavailable'
    return result


def report():
    if not Path('/proc/self/stat').is_file():
        raise SystemExit('Run this inside the Render Linux web service Shell.')
    ticks = os.sysconf('SC_CLK_TCK')
    first = snapshot()
    start = time.monotonic()
    time.sleep(1)
    last = snapshot()
    elapsed = time.monotonic()-start
    processes = []
    for pid, item in last.items():
        old = first.get(pid)
        delta = item['ticks'] - old['ticks'] if old and old['start'] == item['start'] else 0
        processes.append({'pid': pid, 'ppid': item['ppid'], 'task': item['task'],
            'cpu_percent_one_core': round(max(0, delta)/ticks/elapsed*100, 1),
            'rss_mb': item['rss_mb'], 'nice': item['nice']})
    return {'sample_seconds': round(elapsed, 2), 'cgroup': cgroup_stats(),
        'processes': sorted(processes, key=lambda item: (-item['cpu_percent_one_core'], -item['rss_mb']))[:15],
        'note': 'Read-only sample. Per-process RSS includes shared pages; use cgroup memory.current for total. No process arguments or environment variables are printed.'}


if __name__ == '__main__':
    print(json.dumps(report(), indent=2))
