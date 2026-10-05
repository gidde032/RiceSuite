# RiceSuite data location and migration

RiceSuite keeps application data in `~/.ricesuite` by default on a fresh install. `RICESUITE_DATA_DIR` in `ricesuite.env` selects another absolute directory. Run `rice data location` to report the effective paths; it prints no credentials. The directory may not exist until first launch or migration. The checkout's `.ricesuite/` directory holds launcher locks and PIDs and is **not** this data root.

The root has `searcher/` (SQLite library, media cache, saved beat profiles), `clipper/` (jobs and recovery markers), `poster/` (media, queue, history, debug files and **browser profiles** under `sessions/`), `handoff/searcher-to-clipper/` and `handoff/clipper-to-poster/`. A custom Searcher profiles directory outside the old library is copied into `searcher-profiles/`. Existing explicit pillar paths remain effective until migration. `ricesuite.env` remains the configuration and credentials file; prompt assets, source code and unrelated model caches are not data migration inputs.

## Find the directory

On macOS, in Finder choose **Go > Go to Folder** (Shift-Command-G), enter `~/.ricesuite`, then press Return. This opens the dot folder directly without changing hidden-file settings. Enter the configured root instead if you changed it. [Apple: Go directly to a specific folder](https://support.apple.com/en-is/guide/mac-help/mchlp1236/mac).

Windows browsing instructions are for a data copy under a Windows user's home; RiceSuite's application support remains macOS/Linux. Open File Explorer (Windows-E), focus its address bar (Ctrl-L), enter `%USERPROFILE%\.ricesuite`, then press Enter. Use the actual configured or copied path if different. If the folder has the Hidden attribute, Windows 11 offers **View > Show > Hidden items**; Windows 10 offers **View > Hidden items**. A leading dot alone does not mark a Windows folder hidden. [Microsoft: File Explorer](https://support.microsoft.com/en-us/windows/experience/fileexplorer/file-explorer-in-windows).

## Explicit maintainer migration

The maintainer ran this migration on the live machine on 2026-09-29 and confirmed real sessions from the new location. The steps below remain for any other installation with legacy paths. CI checks the commands against fixtures only. Close RiceSuite, all legacy apps and workers, and browser windows using the Rice profiles. The CLI refuses known listeners, leftover suite processes, and Chrome processes advertising the relevant profile path. Keep them stopped from plan through cutover. Do not use a shell-exported pillar path override during migration; put intentional custom paths in `ricesuite.env` so they can be inventoried and switched. The command never stops a process for you.

```bash
rice data plan                    # lists sources, destination, bytes and free space
rice data copy                    # creates an isolated verified copy
rice data cutover                 # updates ricesuite.env and activates the copy
rice data location                # inspect effective paths after cutover
```

Add `--root /absolute/custom/location` to **each** plan, copy, cutover and rollback command if choosing a different destination. Set `RICESUITE_DATA_DIR` in the environment for all commands instead if preferred. The plan refuses existing destinations, nested/overlapping roots, unsupported links or entries, and insufficient space. Allow space for both the retained originals and a complete copy, plus at least 10% copy headroom (minimum 1 MiB). A failed copy leaves the original configuration and data intact and may leave `<root>.migration-stage`; inspect and remove only that isolated partial stage before retrying `copy`. Never treat a partial stage as a migrated root.

`copy` preserves permissions and verifies file hashes before it changes understood absolute references in the **destination**: Searcher's SQLite cache paths and Poster's queued media paths. It copies Clipper's open workspace markers, both handoff stages including dedupe/acknowledgement files, Poster's queue/history, in-flight marker and complete browser-profile directory. It does not open a profile, run a scheduler, ingest a batch, or post. It skips Chrome's transient Singleton lock symlinks and refuses other symlinks. It does not globally edit opaque Chrome databases. `cutover` rechecks the copied data and complete original inventory, backs up the previous config within the private new root, then switches `ricesuite.env`; shell path overrides block the switch. If cutover is interrupted, leave every app stopped and rerun the same `rice data cutover` command to finish the recorded switch. An incomplete marker blocks ordinary startup. Originals are retained and are never automatically deleted. A pending scheduled batch may run through Poster's normal startup catch-up when the maintainer later starts RiceSuite; inspect the queue first.

### Rollback boundary

Before **any post-cutover activity**, with all apps and relevant browsers stopped, run `rice data rollback` (with the same `--root` if used). It compares the copied tree, retained originals and config to their cutover digests; only then does it restore the old config. It retains both trees. After any new activity in either tree, the command refuses rollback because stale originals could lose history or duplicate scheduled work. Stop and reconcile the two histories, queue and handoff markers manually before any switch. A retained original is not a lossless rollback by itself.

### Maintainer acceptance after review

- Confirm `rice data plan` names every expected source, custom path and destination, with adequate space, while all relevant processes are stopped.
- Inspect the copied stores and reported location before starting the app. Keep originals and the config backup.
- On the real machine, verify browser-profile continuity **only with separate explicit authorization for that occasion**. Fixture tests cannot establish authenticated session portability. Do not repeatedly launch profiles for probing.
- Inspect the pending queue before the first restart, then verify library, open Clipper work, handoff dedupe, history and drafts in normal use. Perform any live posting through the usual human gates.

### Removing the originals

`rice` never deletes the originals. Remove them by hand, only after the checks above pass and a real session from the new location has worked. Removal ends the rollback boundary: `rice data rollback` needs the originals unchanged, so it refuses once any are gone.

Before deleting each original, with RiceSuite and the browsers stopped:

- Confirm nothing in it is newer than `<root>/.migration.json` (for example `find <original> -newer <root>/.migration.json` prints nothing). A newer file means something still wrote to the old path after the copy; reconcile it first.
- Compare it against its copy. Every original file should exist in the copy with the same hash. The exceptions are the rewrites described above (Searcher's `library.sqlite3`) and files that changed through normal use after cutover: Poster's history only gains appended lines, handoff dedupe lists only gain entries, and browser profiles refresh cookies and caches.

Keep `.migration.json`, `.cutover.json` and `.pre-migration-env` in the new root. The marker selects the unified paths at startup, and the config backup is the only record of the old settings. It is private (mode 600) and may contain credentials.
