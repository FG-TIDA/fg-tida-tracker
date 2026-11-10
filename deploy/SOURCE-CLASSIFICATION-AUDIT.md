# GitHub source classification review — 6 November 2026

Update, 10 November 2026. GitHub transferred the use-case repository to
[`fg-tida-wg0/use-cases`](https://github.com/fg-tida-wg0/use-cases). Its repository ID
is unchanged. The original findings below describe the pre-transfer state.
Migration 0017 updates the source mapping, and the next sync refreshes the
working group on automatically managed use cases. See [the setup guide](MANUAL-SETUP.md#7-review-and-import-the-public-sources).

This review concerns public GitHub imports, not editor-authored tracker records.
The GitHub repository/collection is separate from a working-group assignment.
Topical similarity, a WG number in prose, or a linked WG document does not prove
that a submission belongs to that WG.

| Public source | Source-backed collection | Source-backed wg assignment |
| --- | --- | --- |
| [FG-TIDA/use-cases](https://github.com/FG-TIDA/use-cases#readme) | Use cases | Not specified. These are Focus Group submissions; no blanket wg0 assignment. |
| [FG-TIDA/themes](https://github.com/FG-TIDA/themes#readme) | Themes | Not specified. Themes are discussed before formal group structuring; no automatic Incubation assignment. |
| [FG-TIDA/meetings](https://github.com/FG-TIDA/meetings#readme) | None added | FG-TIDA. Minutes and presentation types follow the actual source files. |
| [FG-TIDA/marketing](https://github.com/FG-TIDA/marketing) | None added | FG-TIDA. The repository describes presentations, not WG ownership. |
| [FG-TIDA/spec-template](https://github.com/FG-TIDA/spec-template) | Templates | FG-TIDA; no wg assignment. |
| [FG-TIDA/wg-charter-template](https://github.com/FG-TIDA/wg-charter-template) | Templates | FG-TIDA; no wg assignment. |
| [FG-TIDA/wg1-ra-aai](https://github.com/FG-TIDA/wg1-ra-aai#readme) | None added | wg1: its README explicitly identifies it as Working Group 1 work. |
| [wg1 charter](https://github.com/fg-tida-wg1/wg1-cdra-charter) | None added; document type is Charter | wg1. |
| [wg2 charter](https://github.com/fg-tida-wg2/wg2-ibea-charter) | None added; document type is Charter | wg2. |

[wg0's public organization](https://github.com/fg-tida-wg0) identifies the group
and its topic, but currently contains only `.github` community/profile files.
Placeholder charter/specification names are not documents and do not authorize
moving the central use-case collection into wg0.

The public API review found 34 use-case issues and 57 theme issues, excluding pull
requests. None had an explicit `wgN` label. Body references to WG1 or a proposed
WG5 were not treated as ownership or as authorization to create another WG.
Issue labels/states remain GitHub facts, separate from the editor-controlled
tracker approval lifecycle. Supporting attachments are not themselves proposals.

## Correction and future imports

The local plan identified 108 automatic records needing group/category corrections:
34 use-case records assigned to wg0, 59 theme/support records assigned to
Incubation, and unsupported category tags on other automatic records. One manually
managed source was excluded from correction. Nine legacy/editor records without
GitHubSource links were reviewed separately: they already belong to FG-TIDA, with
no unsupported wg assignment. They are not silently attached to a new source.

The correction command defaults to a read-only plan. `--apply` records metadata
corrections and their evidence, without downloading files, changing archive
versions, approving documents, changing accounts, sending mail or writing to GitHub.
Retain a production backup before applying it. Archive hashes/version records are
checked separately in local verification.

After deploying migration `fgtida.0015_githubrepository_category_evidence_and_more`:

```sh
python ietf/manage.py audit_fgtida_classification --skip-checks
python ietf/manage.py audit_fgtida_classification --apply --skip-checks
python ietf/manage.py audit_fgtida_classification --skip-checks
```

Inspect the plan first. Automatic sources receive the reviewed corrections;
manual/excluded/deleted sources and manual repository evidence are preserved.
Older automatic imports without integration rows can also be corrected by their
canonical public GitHub document/issue URL, provided no editor changes are recorded.
This does not recreate a source mapping, duplicate a document or download its file.
No production data changes merely because a code commit is deployed.

Under **Administration → GitHub repositories**, non-root group assignments and
collection categories require GitHub evidence URLs. New unknown sources have no
default Deliverables category. Exact, unambiguous `wgN` issue labels may associate
an issue with an existing numbered group; unknown/multiple labels stay unassigned.
Groups are never invented from prose or labels. Add/verify future groups explicitly.

When a file checksum is unchanged, new classification evidence can update its
metadata without manufacturing a new archived revision. Subsequent ordinary
imports retain observed issue labels/state and show classification references on
the public document page. This review does not activate automated synchronization.
