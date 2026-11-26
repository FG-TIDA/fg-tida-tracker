# Import only new wg0 use cases

Deploy the code containing `--new-only` and `--template-issues`, then open the
Datatracker web service's **Shell** in Render. These commands use that service's
database and persistent document disk, not a separate local database.

First review the read-only plan:

```sh
python ietf/manage.py sync_fgtida_github --repository fg-tida-wg0/use-cases --new-only --template-issues --skip-checks
```

Every issue is listed as `existing; skipped` or `create`. Existing mappings,
deliberate exclusions, deleted-source mappings and records linked by the former
`FG-TIDA/use-cases` URL are skipped. Existing document files, versions, metadata,
numbers and admin overrides are not changed. Pull requests are not imported.

Apply only the missing issues:

```sh
python ietf/manage.py sync_fgtida_github --repository fg-tida-wg0/use-cases --new-only --template-issues --apply --skip-checks
```

The new records receive both Contribution and Use case types under wg0 and
continue its current numbering in GitHub creation order. One record is imported
per issue; linked attachments are not added as separate contributions by this
targeted command. Explicit inline images use the existing restricted GitHub asset
client and are included in the package, or the import stops for review.

Each reading package uses the reviewed specification scaffold and front-matter
order. The original Markdown is retained at `original/github-issue.md`; source
authors, affiliations and any declared X.Y version are retained. Missing source
fields are explicitly marked for review, not invented. This is editorial
formatting, not technical approval. Configured scanning remains required;
`--skip-checks` skips Django startup checks, not file security checks.

Repeat the read-only plan after importing. All previously imported issues should
now report `existing; skipped`. Check **Documents**, filter by wg0 and Use case,
and open a new entry to review its version, credits, reading preview and package.
The importer sends no contribution emails and performs no GitHub writes.

If the command stops, fix the reported configuration/file issue and repeat it.
Successfully imported issues are skipped on retry; do not create duplicates by
hand. No database migration is needed for this command.
