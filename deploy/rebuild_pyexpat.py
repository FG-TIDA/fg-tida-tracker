"""Rebuild only CPython's Expat extension using the fixed system library."""
from pathlib import Path
import shlex
import subprocess
import sys
import sysconfig

source, output = map(Path, sys.argv[1:])
# Keep CPython's generated configuration, but never put its vendored expat.h on
# the include path. The installed Debian development header must be selected.
config = output / 'config'
config.mkdir()
(config / 'expat_config.h').write_bytes((source / 'Modules/expat/expat_config.h').read_bytes())
include = Path(sysconfig.get_path('include'))
target = output / ('pyexpat' + sysconfig.get_config_var('EXT_SUFFIX'))
command = [*shlex.split(sysconfig.get_config_var('LDSHARED')), '-fPIC', '-O2',
           '-I' + str(include), '-I' + str(include / 'internal'),
           '-I' + str(config), '-I' + str(source / 'Modules'),
           str(source / 'Modules/pyexpat.c'), '-lexpat', '-o', str(target)]
subprocess.run(command, check=True)
