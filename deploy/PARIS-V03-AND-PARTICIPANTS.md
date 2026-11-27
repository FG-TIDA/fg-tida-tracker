# Paris agenda v0.3 and participant publication

The reviewed agenda has 30 rows and three public columns: Time, Location and
Event. Session 1 uses the supplied high-level wording. WG2's detailed programme
is distributed across 12:00–12:45 and 14:00–14:45. The final session ends at
17:45 and the dinner runs 17:45–22:00. Times are Europe/Paris (CET, UTC+1).

The reviewed Word file is bundled with the application. Participant data is
not in GitHub; import the authorized CSV privately after deployment.

## Deploy and update the agenda

1. Take the normal Render database/disk backup before changing production data.
2. Deploy this code using **Render → web service → Manual Deploy → Deploy latest
   commit**. Keep the existing pre-deploy migration command. Migrations 0051–0052
   add participant-name/unknown-attendance fields, liaison metadata and session
   material categories; they do not import participant data.
3. Open the deployed web service's **Shell** and review the agenda plan:

   ```sh
   python ietf/manage.py update_paris_agenda --meeting tida-2026-11-30-first-focus-group-meeting --bundled-agenda --skip-checks
   ```

4. Apply the reviewed agenda and update the existing Word download:

   ```sh
   python ietf/manage.py update_paris_agenda --meeting tida-2026-11-30-first-focus-group-meeting --bundled-agenda --apply --skip-checks
   ```

   If more than one agenda document matches, add `--agenda-document IDENTIFIER`
   using the identifier from the existing downloadable agenda's document URL.
   Do not create a duplicate. The local record is named
   `agenda-fg-tida-first-meeting-2026-11-30`; verify the production identifier.

   The bundled archive version is 0.3. If that version already exists with
   different bytes, retain it and choose an increasing `--agenda-version X.Y`;
   do not delete or overwrite old versions. Repeating the command with identical
   Word bytes creates no extra revision. Applying the replacement pauses source
   synchronization for this deliberately edited agenda document.

5. Review the public meeting agenda, both WG2 parts, Session 1, room badges and
   **Download agenda (Word)**. Open **Show meeting materials** on a session and
   check the Agenda, Slides and Minutes tabs. Existing topic/material identities
   are retained where matched; unmatched old sessions remain off the current
   agenda with their materials intact. Session numbers alone never determine a
   material reassignment.

## Transfer the participant CSV privately

On Windows, update the local checkout containing the transfer helper. In Render,
open **Connect → SSH** and copy only its `user@host` destination. In local
PowerShell, run the following, replacing `RENDER_USER@ssh.frankfurt.render.com`
with that destination and adjusting the key path if needed:

```powershell
python deploy/upload_render_import.py --destination "RENDER_USER@ssh.frankfurt.render.com" --identity-file "$env:USERPROFILE\.ssh\fgtida_render_ed25519" --participants "$env:USERPROFILE\Downloads\Registrations_for_FG_TIDA_Data-tracker.csv"
```

SSH prompts for the key passphrase. It is not an API key and is not added to
Render environment variables. The helper sends bytes through encrypted SSH to
`/tmp/fgtida-import/participants.csv`, restricts file permissions and verifies the
SHA-256 checksum. It prints no participant names. Keep the CSV out of GitHub.

## Import and verify the participant list

In the deployed web service's Render Shell, preview:

```sh
python ietf/manage.py import_fgtida_participants --meeting tida-2026-11-30-first-focus-group-meeting --csv /tmp/fgtida-import/participants.csv --publish --skip-checks
```

The supplied file has 388 rows and 384 distinct name/organization/country
records. Four exact duplicate rows are skipped. The `Organitzation` header typo
is accepted. No given/family-name order is guessed. Additional CSV columns are
not imported, and no user accounts or emails are created.

Publish the authorized fields:

```sh
python ietf/manage.py import_fgtida_participants --meeting tida-2026-11-30-first-focus-group-meeting --csv /tmp/fgtida-import/participants.csv --publish --apply --skip-checks
```

Review the meeting's **Participants** page: Full Name, Organization, Country and
Reg Type, with Onsite, Online and total counts above one complete sortable table. New Reg
Type values remain blank. Existing known attendance is retained. Repeating the
import matches existing identities rather than creating duplicate entries.
Existing rows absent from the CSV are retained for administrator review.

Use **Admin → Meeting participants** to correct names, organizations, countries,
Onsite/Online attendance or publication status later. Publication status applies
to both public table rows and aggregate statistics.

## Liaison statements

Use **Documents → Liaison Statement** for the unified catalogue. Filters include
title/body/identifier search, source, destination, start/end dates and Input or
Output. The table has Date, From, To, Deadline and Title. A title opens its
document details and retained original materials.

In **Admin → Documents**, choose Incoming or Outgoing and edit the source
(From), Liaison To, date, deadline, purpose and optional public message. Unknown
metadata remains blank rather than being inferred. Existing incoming/outgoing
listings are reused; originals and their file-security checks are retained.
