# Author identities and complete contribution source packages

Deploy the reviewed changes first. No new environment variables or schema
migrations are required beyond the existing metadata migration 0044.

## Correct metadata

In Render Shell run:

```sh
python ietf/manage.py refresh_fgtida_collections --no-sync --apply --skip-checks
```

The parser now handles Word-converted RST grid tables with a Contact cell such
as `Name: Example Expert / Organization: Example Lab / Country: ... / E-mail:`.
Names, organizations and emails remain separate. Display also normalizes cached
metadata; manual metadata values are formatted without replacing the editor's
chosen identity. Deliberately cleared fields remain cleared.

GitHub use cases and themes take authors/affiliations from the declared document
content, including multiple authors. The combined Proposer section supports
multiple `Name, Organization` lines and shared-organization parentheses. Explicit
Authors fields support comma-separated names. Issue creators' account names are
not guessed to be authors. Older auto-filled values proven to match recorded
imports follow later source updates; deliberate administrator overrides remain
authoritative. If a declaration is ambiguous or absent, correct it in GitHub or
set a deliberate backend override rather than inventing a person or affiliation.

Edit credits in **Admin → Documents → document → Public metadata → Author(s)**,
using commas or new lines. Quote a name or organization containing a comma,
for example `"Family, Given", Another Author` or `"Institute, Paris", Example Lab`.
Explicit assigned author profiles are editable in the author
inline. A name in a source document does not by itself prove a registered identity.

## Submission identity

The public NWI form requires login and binds ownership to that logged-in account.
**Author(s)** and **Affiliation(s)** accept comma-separated or newline-separated
lists. Co-authors do not need accounts. Blank submission fields use the declarations
in the uploaded source. These lists do not change the submitting account.
The form rejects POST fields attempting to select another submitter or proxy
contact. Only the administrator backend can select/correct the submitting account
or record on-behalf-of contact details. Co-author declarations do not automatically
create accounts or link arbitrary namesake profiles.

The recorded owner can open **Edit author details** on a pending NWI and update
authors/affiliations. Other users cannot edit it; adopted/closed items require an
administrator. This updates metadata/history only, without changing the original,
owner, numbering or sending emails. Administrators can edit public metadata and
types in the document backend, and correct the submitting account in Contributions.

Document pages show the submitting account's public profile under **Submitted
by**. A declared author matching that actual submitter or an explicitly assigned
author profile is clickable. External/imported authors remain plain text unless
an administrator assigns their profile. No account identity is inferred by
searching all users for a matching name or email. Profile pages list the person's
authored documents and recorded submissions.

In **Admin → Contributions**, the **Submitter** column and empty/non-empty
Submitter filter show the recorded account. Imported use cases and legitimate
admin on-behalf-of submissions may have none. A plain-text source author is not
evidence that the public login requirement was bypassed. Do not guess ownership
from names or silently assign historical records to namesake accounts.

## New source packages

New uploaded RST packages retain their original RST sources and supplied media;
missing reviewed template configuration, styling, logos and dependency files
are added automatically. Word conversion already produces a complete package
and extracts embedded media. Neither uploaded conf.py nor JavaScript is executed
on the tracker. Existing GitHub snapshots remain faithful to their source; this
does not modify any GitHub repository or build workflow.

## Repair older incomplete packages

### Word contributions, including ones with no ZIP yet

The audit checks template assets and compares the embedded media in the retained
Word original with the actual bytes in its ZIP. A template profile label alone
does not prove completeness. New Word conversions retain embedded media even
when Pandoc does not reference it in the converted text. This does not guarantee
that complex Word diagrams render correctly; compare the conversion with Word.

After deploying this code, use the Render **web service → Shell**. Take a database
and persistent-file backup first. These commands run sequentially at lower CPU
priority, suitable for a quiet period on the one-CPU web service:

```sh
nice -n 10 python ietf/manage.py complete_fgtida_packages --word-only --skip-checks
```

Review the per-contribution states. Then repair all eligible Word records:

```sh
nice -n 10 python ietf/manage.py complete_fgtida_packages --word-only --apply --reason "Complete Word source packages and recover retained embedded media." --skip-checks
```

Run the first command again. Its summary should show `complete` for repaired
records. Inspect each ZIP and compare its preview with the Word original before
marking the conversion verified in admin. The ZIP includes `index.rst`, the
contribution chapter, build configuration, styling, template assets and embedded
media when the original contains any. A text-only Word original has no figures
to extract. New conversions have the same completeness check automatically.

States such as `awaiting_security_checks`, `later_source_requires_review`,
`no_document` or `no_current_zip` are unresolved, not successful repairs. Do not
overwrite newer edits or bypass scanning. Review those contribution IDs in admin;
missing originals, legacy `.doc` files and unsupported embedded formats may need
the contributor to supply a DOCX or supported image export. Earlier historical
versions remain immutable and can still contain the older package.

### Other incomplete source packages

Take a production database and file backup first. Plan without changes:

```sh
python ietf/manage.py complete_fgtida_packages --skip-checks
```

The plan excludes use cases and reports complete, incomplete, awaiting security
checks, missing current ZIPs, or later source edits requiring individual review.
After reviewing the plan, repair one selected contribution (use its backend ID):

```sh
python ietf/manage.py complete_fgtida_packages --contribution 22 --apply --reason "Reviewed the retained original and incomplete source package." --skip-checks
```

Contribution 22's currently published 0.1 package was already complete when
inspected on 20 November 2026, so it should remain unchanged. To process all
eligible incomplete records after review, omit `--contribution 22`.

Alternatively open **Admin → Contributions**, select at most ten reviewed
records and choose **Complete reviewed contribution ZIPs with specification-template
assets**. Click **Go**, provide a reason, read the acknowledgement and confirm.

Repairs keep the same contribution/document identifiers and numbers, preserve
original uploads and earlier versions. Rebuilding an existing package records a
new archive version; converting a previously unconverted Word document adds its
RST ZIP alongside the existing Word version. They
send no emails, write nothing to GitHub and do not grant WG approval. An older
Word conversion is rebuilt from its retained Word original so embedded images
can be recovered; verification resets and must be reviewed against that original.
Later source edits are not overwritten. Generic RST repairs preserve all existing
source/media bytes and add supporting assets only. Missing original diagrams
cannot be invented or recovered if they were never supplied.

Inspect the new default version and its ZIP after repair. An explicitly selected
old historical version retains its old package; earlier archives are immutable.

## Reading previews and abstracts

The submission form does not request a separate abstract. Existing catalogue
abstracts remain editable in the backend, but are hidden in a reading preview
when the source already contains an abstract section or the same summary text.
Original documents and historical versions are not rewritten for this change.

Figures resolve relative to the RST chapter that references them. Plain local
RST/text includes are expanded in memory within bounded size/depth limits, so
their figures retain the correct paths and included chapters do not appear twice.
Includes with unsupported options, missing assets and include cycles show notices;
the renderer never reads the server filesystem or fetches external images.

Mermaid, Graphviz, PlantUML/UML and draw.io directives can use a supplied static
image export. For example, `diagram.mmd` and `diagram.svg` in the same folder,
referenced with `.. mermaid:: diagram.mmd`, show that SVG after the normal safety
checks. A diagram without a supported export shows its source and a notice,
rather than disappearing. Uploaded diagram programs/configuration are not run.
Keep a PNG/JPEG/GIF/safe-SVG export in the package for a visual preview. Scan and
revision permissions continue to apply to every image URL, including direct URLs.
