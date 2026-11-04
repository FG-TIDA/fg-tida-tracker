# Native library security backports

The pinned stable Python/Debian image contains Expat 2.9 and zlib 1.3.1. At the
time of this update, Debian lists the Expat fixes in source version `2.10.0-1`
and the zlib fix in `1:1.3.dfsg+really1.3.2-4`, before stable binary updates.

The isolated `native-security` build stage enables **source packages only** from
Debian sid. It fetches exact versions through APT's signed archive metadata and
builds them against stable trixie libraries/toolchains. It does not install sid
binary packages or add sid repositories to the runtime. Only the resulting
`libexpat1` and `zlib1g` runtime packages enter the final image. Package builds
run their configured tests; an explicit Expat `make check` is also required.
Compilers, development headers and source trees stay in the build stage.

Python's `pyexpat` extension in the base image embeds a separate Expat copy.
Updating the system library alone leaves that parser vulnerable. We download
the exact matching CPython release, verify the SHA-256 from the pinned official
image, and rebuild only this extension against the patched shared library.
The build fails unless a fresh Python process reports Expat 2.10 or newer and
parses XML successfully. zlib's Debian patch includes the upstream non-blocking
`gzwrite` fix, not just the unpatched 1.3.2 release.

These are project-built security backports, not Debian stable binary releases.
Maintain this recipe until the official stable/Python images include the fixes;
then return to those vendor binaries and remove the temporary build stage.
Rebuild, rescan and run the FG-TIDA regression/upload/PDF/media tests whenever
updating the base image or these versions. Exact source pins deliberately fail
if unavailable rather than silently accepting a different source revision.

Keep raw scanner findings. A stable-distribution advisory may use an unbounded
affected-version range while its fix is awaiting the stable update, even when
the compiled source already contains the upstream fix. Compare the installed
package version, source patch and actual Python parser version; do not globally
ignore Expat/zlib or change package metadata to make the scanner quiet.

## Recorded scan result

The 4 November production image scan indexes 453 packages and retains **44 raw
findings: three high, one medium and 40 low**. No blanket exceptions were applied.
The three high entries explicitly identify the newly installed `expat@2.10.0-1`
and `zlib@1:1.3.dfsg+really1.3.2-4`, but their Debian 13 affected range is `>0`.
Thus these stable-distribution rules also match the fixed backport versions.
The repair evidence is the authenticated fixed source revisions, actual Python
Expat version, confirmed upstream zlib patch, and passing library/application
tests; the raw scanner count is not claimed to be zero.

The medium finding, CVE-2025-45582, concerns repeated extraction of specially
crafted TAR archives into a shared directory. Debian records the issue as
disputed. FG-TIDA does not accept TAR uploads or run that extraction workflow;
its session-material TAR endpoint creates downloads. Operator backup restores
must still use verified archives and an isolated target as documented in the
recovery guide. Other low vendor findings remain tracked and are not declared
fixed by these changes. Reassess the raw image report after vendor updates.

Reference: [Debian tar advisory](https://security-tracker.debian.org/tracker/CVE-2025-45582).

References:

- [Debian Expat NULL dereference advisory](https://security-tracker.debian.org/tracker/CVE-2026-117610)
- [Debian Expat overflow advisory](https://security-tracker.debian.org/tracker/CVE-2026-118370)
- [Debian zlib advisory](https://security-tracker.debian.org/tracker/CVE-2026-85091)
- [Upstream zlib fix](https://github.com/madler/zlib/commit/df84af25dc1942490e1d1c899a07619152a46148)
