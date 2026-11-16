# Connect working-group repositories to Datatracker

Datatracker records submission, adoption and publication decisions. GitHub hosts
the approved working group's source, pull requests and release builds. The
tracker can stage a contribution in a review PR and update its lifecycle label
in GitHub. GitHub sends catalog changes, working PDFs and tagged archives back.
[FG-TIDA workflow](https://fg-tida.github.io/.github/)

## 1. Deploy and register the GitHub App

1. Deploy the tracker code with the new training migration. Keep Render's
   pre-deploy command set to `python ietf/manage.py migrate --noinput --skip-checks`.
2. In GitHub, open **Settings → Developer settings → GitHub Apps**. Reuse your
   existing tracker App or create one as described in the [manual setup](MANUAL-SETUP.md#4-register-the-github-app).
   For an existing-repository connection, grant **Contents**, **Pull requests**
   and **Issues** read/write and **Metadata** read. Subscribe to **Push** and
   **Issues**. The webhook URL is
   `https://tida-datatracker.org/integrations/github/webhook/`.
3. Keep the App PEM and webhook secret in **Render → Environment → Secret Files**
   and enter the App ID and file paths as shown in the [Render setup](MANUAL-SETUP.md#5-mount-secrets-and-configure-render).
   Keep `FGTIDA_GITHUB_WRITE_ENABLED=false` while reviewing existing mappings.
4. In **GitHub App → Install App**, install or update the App for **FG-TIDA**,
   **fg-tida-wg0**, **fg-tida-wg1** and **fg-tida-wg2**. Select the tracker-managed
   repositories in each organization. After a transfer, check access in the
   destination organization's installation. The organization owners approve
   the installation and any new permissions.

## 2. Map organizations and review moved repositories

1. In **Render → Shell**, run
   `python ietf/manage.py configure_fgtida_github --skip-checks`. This prepares
   editable defaults and preserves existing mappings. It makes no GitHub writes.
2. Go to **Datatracker Admin → GitHub accounts**. Open each organization and
   enter the number from its GitHub installation settings URL
   (`/settings/installations/NUMBER`). The four installation IDs are distinct
   from the App ID. Under **Working group configurations**, verify each display
   number, topic acronym, group and linked GitHub account.
3. Under **GitHub repositories**, check owner, repository name, GitHub ID,
   default branch, WG, sync mode and allowed paths. These are the public
   destinations checked on 16 November 2026.

   | Owner | Repository | Mode and path |
   | --- | --- | --- |
   | FG-TIDA | themes | Theme / use-case issues |
   | fg-tida-wg0 | use-cases | Theme / use-case issues |
   | fg-tida-wg0 | wg0-uct-charter | Document files, `charter.md` |
   | fg-tida-wg1 | wg1-cdra-charter | Document files, `charter.md` |
   | fg-tida-wg1 | wg1-cdra-ra-aai | Versioned work item, `src/doc/index.rst` |
   | fg-tida-wg2 | wg2-ibea-charter | Document files, `src/doc/index.rst` |

4. Use [GitHub's repository endpoint](https://docs.github.com/en/rest/repos/repos#get-a-repository)
   to compare its numeric `id` with Datatracker. The current wg0 use-case ID is
   `1354561082`. The current wg1 RA-AAI ID is `1378915670`. If the ID is on
   an older mapping, edit that record's **Account**, **Name**, **Group** and
   **Group evidence** to match the verified destination. Reusing the row preserves
   its links, revisions and sync history. If IDs differ, keep the old record
   disabled and use a separate mapping for the replacement. Review disabled,
   manual and excluded records individually. Check **Enabled** on a repository
   mapping when you are ready for it to sync.
5. Under **Work items**, link an existing tracker document to its official
   versioned repository. Confirm the document's WG matches the repository's
   organization. Supply the exact default branch and **Publish workflow**
   path, commonly `.github/workflows/docs.yml`.

## 3. Verify inbound issue and document sync

For each enabled source, run a preview in **Render → Shell**, then repeat with
`--apply`. For example:

```sh
python ietf/manage.py sync_fgtida_github --repository FG-TIDA/themes --skip-checks
python ietf/manage.py sync_fgtida_github --repository FG-TIDA/themes --apply --skip-checks
python ietf/manage.py sync_fgtida_github --repository fg-tida-wg0/use-cases --apply --skip-checks
```

On an existing automatically managed issue, confirm the author and affiliation
where GitHub supplies them. A metadata-only refresh keeps the current archive
revision. In GitHub App settings open **Advanced → Recent deliveries** and check
that a supported event received HTTP 200. In Datatracker inspect **GitHub jobs**
and **GitHub repositories → Last sync**. The regular polling job can also
refresh the catalog.

## 4. Verify outbound sync on one adopted contribution

1. In **Admin → Contributions**, record the plenary's allocation or Incubation
   decision in the review note. Leave the submission pending until adoption.
   Select the adopting WG in **Adopt into** for an Incubation submission or
   whenever the allocation differs from the declared target. If the declared WG
   adopts it, you can leave the override blank. The original target remains in
   the submission history. Plenary decisions and the six-month Incubation rule
   are handled by editors.
2. For the first test, create the official repository yourself from the
   [New Work Item Template](https://github.com/FG-TIDA/spec-template). Choose the
   adopting WG organization and name it `wgN-topic-short-name`. Include it in that
   organization's App installation. Configure the repository in Datatracker
   and link it to the adopted **Work item**. This route works with the App
   permissions in step 1. Automatic repository creation is available when the
   organization approves the required template-generation permissions.
3. In **Render → Environment**, enable `FGTIDA_GITHUB_WRITE_ENABLED=true` and
   redeploy. A contribution adoption queues a setup job. For a work item
   that needs a fresh job, select it in **Admin → Work items** and choose
   **Queue repository creation / metadata pull request**. Check existing pending
   outbound jobs before enabling writes, since they become eligible to run.
4. Run `python ietf/manage.py process_fgtida_github --limit 5 --skip-checks`.
   Check **GitHub jobs** for completion. In the destination repository open
   **Pull requests** and review `.tracker/metadata.json`, the recorded source
   and assets, and the Datatracker badge. Merge through the normal review
   process. Use branch protection on `main`.

## 5. Verify working builds and releases

1. Follow [the Actions setup](MANUAL-SETUP.md#8-install-build-publishing-using-a-pull-request)
   in the official repository. Use the existing build's actual PDF path and a
   pinned commit for the publisher action. Give the publishing job
   `contents: read` and `id-token: write`. Publish from the configured branch and
   `vX.Y` tags. Keep publishing outside pull-request runs.
2. In **Render → Environment**, set `FGTIDA_GITHUB_INGEST_ENABLED=true` and
   redeploy. In GitHub merge a reviewed change to `main`. Verify the publish
   step in **Actions**, then open the tracker document and confirm the working
   PDF matches that commit.
3. After the WG agrees a release, create the next `vX.Y` tag on a reviewed
   commit. Verify one matching Datatracker archive with its PDF, source and
   manifest. Record the WG's decision in Datatracker and verify its GitHub label.
4. When both directions pass, enable `FGTIDA_GITHUB_SCHEDULE_ENABLED=true` on
   the Render web service and redeploy. Verify one queued test job completes
   without a Shell command.

For a future WG, add the group in Datatracker, install the App in its organization,
create its **GitHub account** and **Working group configuration**, then register
each official repository and repeat the one-item test. Use distinct WG numbers
and set each new work item's repository under the adopting group.
