#!/bin/sh
# Build security-fixed Debian sources against stable Debian, never sid binaries.
set -eu
export DEBIAN_FRONTEND=noninteractive
printf '%s\n' 'deb-src http://deb.debian.org/debian sid main' > /etc/apt/sources.list.d/security-source.list
apt-get update
apt-get install -y --no-install-recommends build-essential dpkg-dev ca-certificates curl
apt-get build-dep -y --no-install-recommends expat=2.10.0-1 zlib=1:1.3.dfsg+really1.3.2-4
mkdir -p /sources /out/packages
cd /sources
apt-get source --only-source expat=2.10.0-1 zlib=1:1.3.dfsg+really1.3.2-4
cd /sources/expat-2.10.0
dpkg-buildpackage -b -uc -us -j2
cd /sources/zlib-1.3.dfsg+really1.3.2
dpkg-buildpackage -b -uc -us -j2
cp /sources/libexpat1_*.deb /sources/zlib1g_*.deb /out/packages/
dpkg -i /sources/libexpat1_*.deb /sources/libexpat1-dev_*.deb
ldconfig
# The official Python image embeds its own old Expat. Rebuild that extension
# from the EXACT matching, SHA-verified CPython release against the fixed library.
cd /sources
curl -fsSLO "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz"
echo "${PYTHON_SHA256}  Python-${PYTHON_VERSION}.tar.xz" | sha256sum -c -
tar -xf "Python-${PYTHON_VERSION}.tar.xz"
python /build/rebuild_pyexpat.py "/sources/Python-${PYTHON_VERSION}" /out
PYTHONPATH=/out python -c "import pyexpat; assert pyexpat.version_info >= (2, 10, 0); p=pyexpat.ParserCreate(); p.Parse('<root>safe</root>', True); print(pyexpat.EXPAT_VERSION)"
