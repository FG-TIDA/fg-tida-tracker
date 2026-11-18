# Word conversion scaffold

`spec_scaffold.json` vendors documentation support files from the public
`FG-TIDA/spec-template` repository at commit
`f739b7d2461ecf963248f004f058f73e4fded26d`.

Source: https://github.com/FG-TIDA/spec-template/tree/f739b7d2461ecf963248f004f058f73e4fded26d

Each file is base64-encoded with its SHA-256 checksum. The original MIT license
is included in the bundle and every generated ZIP. No credentials or contributor
data are included. The template's example chapters and index are omitted;
`word_scaffold.py` writes an index and the actual converted Word chapter instead.
Extracted media is placed under `images/word/` and references are adjusted.

The bundle is loaded as data, without fetching GitHub at conversion time or
executing `conf.py`, JavaScript, HTML templates or dependency installation.
The validator's narrow exceptions for template JavaScript/HTML require exact
paths and matching reviewed bytes. To update the upstream snapshot, review all
changed files, retain its license, regenerate the checksummed bundle and run
the source-package, preview, conversion and adoption tests plus a separate
Sphinx build. Never update this bundle from an untrusted uploaded ZIP.
