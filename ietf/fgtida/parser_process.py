"""Document parsers receive runtime paths and locale, without application credentials."""
import os


def environment():
    permitted = {'PATH', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR',
                 'PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'PYTHONNOUSERSITE', 'LD_LIBRARY_PATH'}
    values = {key: value for key, value in os.environ.items() if key in permitted}
    # Parser subprocesses do not need a Git-derived application version. Avoid
    # repository walks during isolated-parser startup, especially on bind mounts.
    values['FGTIDA_RELEASE_VERSION'] = 'parser'
    return values
