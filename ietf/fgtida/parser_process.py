"""Document parsers receive runtime paths and locale, without application credentials."""
import os


def environment():
    permitted = {'PATH', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR',
                 'PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'PYTHONNOUSERSITE', 'LD_LIBRARY_PATH'}
    return {key: value for key, value in os.environ.items() if key in permitted}
