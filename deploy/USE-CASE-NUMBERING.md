# Use-case contribution numbers and multiple types

The normal deployment migrations add an Additional types field and number
existing WG0 use cases after the current highest WG0 contribution number.
Existing numbered contributions keep their numbers. Unnumbered use cases are
appended in document timestamp/ID order, using the current WG0 sequence rather
than a separate counter. Subsequent submissions continue after that batch.

The migration identifies use cases through existing source tags, their explicit
Use case type or the configured fg-tida-wg0/use-cases GitHub source. It reuses
existing document and contribution records. It does not create new document
versions, change approval states or send submission notifications. Imported
numbering records show Imported from GitHub in Admin → Contributions; the
original file remains in the existing document archive.

Future synchronized WG0 use cases receive both Contribution and Use case types
and a number on their first import. Repeated syncs reuse the same number.
Deleting/reordering contribution entries follows the existing display-number
rules, while document IDs and GitHub linkage remain stable.

To edit document labels, open Admin → Documents → the document → Public metadata.
Use Public type for its primary label and Additional types for any further labels.
Both labels appear in public document lists and both type filters find the record.
Category is not restored. Admin → Contributions retains the editable numbering
position. Keep imported file/version edits in the linked document and source
workflow, rather than uploading a duplicate contribution.
