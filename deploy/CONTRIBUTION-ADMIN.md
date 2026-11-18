# Backend contributions and Word conversion

Deploy the reviewed code after configuring the private scanner for ordinary
user uploads. Keep the existing pre-deploy migration command; migration 0036
adds contributor contact details and the administrator-trust scan label.

## Add a contribution for someone without an account

1. Sign in to the admin backend and open **Contributions → Add contribution**.
2. Leave **Submitter** empty. Enter **Contact name**, **Contact affiliation**
   and, if provided, **Contact email**. A name is required when no account is
   selected. No login account or synthetic password is created.
3. Enter the title, abstract and the relevant working group, or choose
   **Incubation** for a new work item outside existing groups.
4. Choose **RST source ZIP**, **Word (.docx)** or the GitHub source route and
   complete the relevant source fields. For Word, upload the DOCX and leave the
   entry-document/source-folder fields empty. For an RST ZIP, specify its
   actual entry document and include every referenced image/supporting file.
5. Confirm publication consent and save. The regular numbering/work-item
   workflow applies. The group receives its configured notification and the
   supplied contact email receives the receipt. Emails contain links only.

Contact names, affiliations and email addresses become document metadata and
can appear publicly. Obtain the contributor's consent. A guest contact uses
the public document link; private submission management requires an authorized
account. Administrators can edit the contact fields later in the contribution
record. Use the linked document's author/attribution fields for additional
co-authors and other document metadata.

New Word submissions already convert automatically to an RST package. Keep the
original Word document and compare the converted text, tables, equations,
references, figures and diagrams with it. Conversion preserves extracted media
but cannot guarantee that every Word-native drawing or complex layout survives.
Export missing diagrams as images and correct the RST package if necessary.

The ZIP now includes the reviewed specification-template documentation scaffold:
`index.rst`, the converted `chapters/01_contribution.rst`, `images/` (including
extracted Word media), `_static/`, `_templates/`, `conf.py`, the dependency list
and template license. Example chapters are replaced by the actual contribution.
`conversion.json` records the pinned template commit; conversion does not fetch
or execute GitHub code. This is a documentation source package, not a copy of
the repository's GitHub Actions or development environment. Review its metadata
before building or publishing. Official repository build files remain in place
when the contribution is adopted into a repository.

### Upgrade an earlier minimal Word conversion

After deployment, existing ZIPs are retained unchanged. To upgrade one:

1. Open **Admin → Contributions**, select up to ten earlier Word conversions,
   choose **Rebuild Word packages with the specification template**, then **Go**.
2. Read the confirmation, enter a review reason and acknowledge the rebuild.
3. A new package version is archived. Earlier archives, the original Word file,
   contribution identity and public number are retained. No notifications are
   sent and approval is unchanged. Conversion verification is reset.
4. Compare the new package and preview with the original before verifying it.

The action refuses to replace a package when the current document has later
source edits or its recorded checksum no longer matches. Already upgraded
packages are skipped. For Word-only records without a source ZIP, use the
conversion action below instead; that first conversion retains the existing
document version.

## Convert existing Word-only contributions

1. In **Admin → Contributions**, select up to ten Word-only contributions.
2. Choose **Convert selected Word contributions to RST ZIP packages** and click
   **Go**. The action supports DOCX; older binary DOC files must first be saved
   as DOCX and deliberately archived through the normal revision workflow.
3. Successful conversions add a maintained **RST ZIP package** alongside the
   existing Word version. The contribution ID, public number, existing version,
   original Word file and work-item identity are preserved. Repeating the action
   does not duplicate contributions. Conversion alone does not approve work or
   send new notifications.
4. Open the contribution's preview and ZIP. The public document preview uses
   the maintained RST package, including referenced images; its formats include
   the Word original and ZIP. If the current document has advanced beyond that
   original Word file, the action stops and requests revision review instead
   of attaching older content to the newer version. A conflicting ZIP already
   attached to the same version is also preserved for review.
5. Compare the conversion with the original. In the contribution change form,
   select **I compared the converted RST text, figures and package…** and save.
   The administrator and verification time are recorded. A registered submitter
   can also verify their own conversion. Adoption requires verification for
   Word contributions.

Existing files with unknown upload provenance still require a working scanner
or a reviewed exact-file exception before conversion. New administrator-uploaded
files and their validated derivatives use the requested administrator-trust
policy and skip antivirus scanning.

## Manage the unsafe-file area

Open **Admin → File scans**, filter **Status** to **Malware rejected**,
**Unsafe document structure** or **Scanner unavailable**, and open the record
to see its reason and history.

- To unblock a reviewed antivirus false positive, select one file, choose
  **Approve a reviewed antivirus false positive (one file)**, then follow the
  password/reason/acknowledgment form. Structural failures cannot be overridden.
- To retry after fixing the scanner or updating signatures, choose **Review
  and retry checks for selected files** and supply the investigation note.
- To delete privately held upload bytes, choose **Remove selected held files
  from private quarantine**. This does not delete published work items or erase
  the audit record. Removing bytes does not make a rejected file safe.
- To remove a published contribution/document, use its normal document delete
  action after reviewing the related objects. This is separate from quarantine.

See [File scanning setup and reviewed exceptions](UPLOAD-SECURITY.md).
