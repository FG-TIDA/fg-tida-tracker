# Verified contact revisions, duplicate review and public-read capacity

Deploy the reviewed code with the normal migration pre-deploy command. Migration
0053 adds a reversible duplicate-to-original relationship; it merges no live data.
No accounts are created, activation emails sent, or GitHub sources modified by it.

## Administrator-entered contributions

Set the exact Submission contact email in Admin - Contributions. The contributor
can create their own account with that address and complete the normal email
activation. Only then does the item appear in My submissions and offer revision
access. Original submitter attribution remains unchanged. Co-author lists and
unverified account/email strings never grant ownership. An email already attached
to a real account must use sign-in or password recovery, not a second account.

For a Submitted contribution, use My submissions - open original - Submit a new
version. The Word, RST ZIP and GitHub routes retain their security and increasing
version checks. Adopted/closed work keeps its existing administrator/WG workflow;
this change does not bypass approvals. An inactive or unverified contact account
does not get access to private originals, conversion checks or author editing.

## Existing duplicates: review before applying

Take a database and persistent-file backup. In Render Shell:

```sh
python ietf/manage.py review_fgtida_duplicates --skip-checks
```

This read-only report includes record IDs, public numbers and titles, not contact
emails. Candidates are not proof of duplication. In particular, WG0-019 and
WG0-020 refer to different GitHub issues and require source review; this command
refuses to consolidate automatically managed sources.

WG0-044 is record 65; WG0-070 is record 124. Their public histories identify the
same author's corrected re-submission. Verify those IDs and current versions,
then preview the consolidation:

```sh
python ietf/manage.py review_fgtida_duplicates --primary 65 --duplicate 124 --archive-version 0.2 --skip-checks
```

Both currently have different 0.1 packages. They are not overwritten: the corrected
package gets an explicit editorial archive version 0.2 in History, with the entire
author History and technical text retained. The untouched submitted package stays
in the old record and in the primary record's private original archive. The later
submission timestamp is recorded in metadata. This does not imply approval.

After reviewing the plan, apply that specific pair:

```sh
python ietf/manage.py review_fgtida_duplicates --primary 65 --duplicate 124 --archive-version 0.2 --reason "Verified author re-submitted the corrected WG0-044 at the Secretariat's request; retain original packages and histories." --apply --skip-checks
```

If 0.2 is already used, stop and review; do not overwrite it. The duplicate record
is retained, suppressed from catalogue lists, and its current document page links
to the original work item. Earlier explicit revision downloads and histories stay
accessible under their existing security checks. No messages are sent; unsent
duplicate notifications are cancelled. Repeating the same pair is a no-op.
There is no bulk automatic deletion or title-based merging.

## Performance checks for the 2 CPU / 4 GB service

Session icons/dialogs share one checked inventory, scan verdicts are read in a
batch per session, and version metadata is prefetched. Document downloads hash in
bounded chunks and stream an open file, instead of allocating a full-file copy.
Scanner verdicts are still checked on every request; permission responses are not
shared across users. These improvements are not a certified 300-user capacity.

The worker default remains 2. On a staging service with representative restored
data, test the default first, then optionally set WEB_CONCURRENCY=2 and
FGTIDA_WEB_THREADS=4. This enables 8 request threads across two processes, not
eight CPUs. Include background sync/mail jobs and check database connection limits
and web/database CPU, memory, timeouts and 5xx errors. Roll back the thread setting
if response times or resource pressure worsen. Never disable scanning to improve
benchmark results.

From a separate machine, use the bounded GET-only probe against your authorized
staging origin, increasing clients 10, 25, 50, 100, 200, 300 only while previous
steps remain healthy:

```sh
python deploy/load_public.py --base-url https://YOUR-STAGING-HOST --allow-remote --clients 25 --requests 250 --path /doc/ --path /meeting/tida-2026-11-30-first-focus-group-meeting/agenda/ --path /meeting/tida-2026-11-30-first-focus-group-meeting/participants/
```

Also test a representative clean document's `/doc/IDENTIFIER/download/VERSION/`
path. Monitor outbound bandwidth as well as CPU. Keep headroom below saturation;
300 readers and 300 simultaneous large downloads are different workloads. This
short probe is a smoke test, not a long-duration browser-user or resilience test.
Do not run a 300-client stress test against the live meeting service.
