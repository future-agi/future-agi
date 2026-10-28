# bin/update: signed, verified updates for Docker Compose installs

`bin/update` moves a Standalone, Distributed or production (`deploy/setup.sh`)
install to a newer Future AGI release. It runs on the Docker host, from the
checkout the install uses. It never runs inside the application, never gives a
container the Docker socket, and never touches Kubernetes (the Helm chart is
upgraded with Helm or GitOps; see `deploy/helm/futureagi/README.md`).

It only ever changes the image version keys of the env file
(`FUTURE_AGI_VERSION`, `FRONTEND_VERSION`, …), and on those lines only the
value: comments, quotes, `export`, line endings and every other line of `.env`
or `deploy/.env.production` stay byte for byte as they are. An update that
fails before anything restarts restores the file exactly as it found it, unless
another process edited it meanwhile or the restoration could not be made durable.
In those cases it preserves the journal and refuses to guess. At the final
commit boundary an operator's file can move to private retention while the
updater candidate becomes live: both entries survive and the run stops on an
observed conflict (see recovery below). This is preservation, not compare-and-swap.

## What an update does

1. **Verify the release.** Each release publishes `future-agi-update-manifest.json`
   and its Sigstore bundle on the GitHub Release. `bin/update` accepts the
   manifest only if it was signed by this repository's
   `release-update-manifest.yml` workflow, run by this repository at that
   release's own tag (keyless Sigstore, GitHub Actions issuer). The manifest
   cannot name its own signer.
2. **Plan.** It finds the installed version of every Future AGI image (the tag
   in the env file, or, for images that follow `:latest`, the version label of
   the running image) and refuses the update when:
   - the release is older than or equal to what runs (it never downgrades),
   - the install is older than the release's `minimum_version`
     (`deploy/update/upgrade-floor`),
   - the jump is larger than the policy allows (default `minor`),
   - the checkout's compose files and bind-mounted bootstrap files differ from
     the release's (`deploy/update/deployment-files.txt`),
   - `.env` lacks a setting the release's `.env.example` adds,
   - the install runs images built from source (`local`), or another stack
     than the env file selects, or a compose file pins an image by digest,
   - a service's image is not selected by its version key (a compose override
     sets the tag itself, or the image comes from a path bin/update cannot
     verify), or a version key's value is not a plain tag (`${OTHER}`),
   - the env file names another version than the running containers (it was
     edited without restarting the stack: put the key back, or restart first),
     or the containers of one image run different versions,
   - containers of a service whose Compose profile is not active are running
     (they would stay on the old release: enable the profile or remove them).
3. **Back up.** It runs your backup hook and stops if the hook fails.
4. **Update.** It writes the new version tags, checks the resolved Compose
   configuration, pulls the images (and any third-party image the release
   adds), and refuses to start any image whose registry digest differs from
   the signed one. A version key pinned for a
   service whose Compose profile is not active (production pins
   `SIMULATION_RUNNER_VERSION` for the `workers` profile) moves to the
   release too; that image is pulled when you enable the profile.
   `apply --stage` stops here: the env file selects the release and its
   verified images are on the host, but nothing restarted (see production
   below).
5. **Restart and verify.** Standalone restarts with `docker compose up -d` and
   waits the way `./bin/install` does: migrations extend the wait up to
   `INSTALL_READY_MAX_SECONDS`. Distributed and production use
   `up -d --wait` like `./bin/install` and `deploy/setup.sh`. `up` never
   pulls again (`--pull never`, where Compose supports it), so only the
   verified images start. Then `/health/` must answer and every container
   must run the new image. One-off `docker compose run` containers (such as
   initialization jobs) are left out of these checks.

Each run leaves a receipt in `.futureagi-updates/`.

Before a mutating run, including recovery by `rollback` without `--yes`, the
updater proves ownership under the checkout's update lock. Every container of
the selected project must have coherent Compose provenance: the canonical
working directory, selected compose files in order, and the explicit env file
when one was used. Stopped containers and one-off initialization jobs count
for ownership even though they do not count as running application versions.
Names, image versions and a matching project label alone are not proof. The
check is repeated before mutating Compose commands; it is not a cached grant.
`status` and no-stack guidance remain read-only and usable without an owner,
but absent evidence cannot authorize an update or journal recovery.

If `bin/install` used an exported `COMPOSE_PROJECT_NAME` without recording it,
persist the intended project in this install's actual env file (the selected
`--env-file` for production). Only unset an unrelated export: unsetting the
intended name can select another checkout's default project. Empty names use
Compose's fallback. Empty `COMPOSE_ENV_FILES` is equivalent to unset; nonempty
values are refused outside production, which uses its explicit env file.

Missing, conflicting or relocated provenance is refused with the owning paths
in the diagnostic. Run from the original checkout with its original config
and env file, or restore operator-verified symlink-equivalent paths. Absolute
and working-directory-relative env-file label paths are checked canonically. An explicit file with no label
cannot be proved: verify and repair the installation manually using a Compose
release that records it. There is no adoption flag, automatic deletion, or
ownership bypass.

An install that followed `:latest` (empty version keys) is pinned to the
release's version tags by its first update. On a Distributed install that
includes `FI_COLLECTOR_VERSION`: `./bin/install` then pulls the published
fi-collector instead of building it from the checkout. From then on `./bin/install` keeps
starting the pinned versions, even after `git pull`: keep upgrading with
`bin/update`, or set the keys yourself (or empty them to follow `:latest`
again) before re-running `./bin/install`.

## Update a Standalone or Distributed install

```bash
cd /path/to/your/checkout

# 1. Configure a backup once (see "Settings"). Without one, apply refuses
#    unless you pass --allow-no-backup.

# 2. Move the checkout to the release's compose files. Your .env is not
#    tracked by git and stays as it is.
git fetch --tags
git checkout v1.53.0          # or `git pull` if you follow main

# 3. If the release adds settings, merge them into .env (starts nothing):
./bin/install --no-up -y

# 4. See the plan, then apply it.
./bin/update check
./bin/update apply
```

`apply --stage` does everything up to the restart and stops: it backs up,
pins, pulls and verifies, and the next `apply` finishes it without a second
backup. Stage right before the restart you plan: the database migrations run
at that restart, and the backup they would be restored from is the one taken
when you staged.

`check` changes nothing. It prints the next command to run, or why the update
is blocked: each reason names what fixes it. Step 3 is for a `.env` that
`./bin/install` wrote; if yours uses `export`, indentation or spaces around
`=`, `check` names the missing keys and you copy them from `.env.example` by
hand.

## Update a production install

Production installs are configured by `deploy/setup.sh`, and their bootstrap
jobs only check the schema: the release's initialization jobs apply it, and
they have to run on the new images before those images start. So a production
update has two steps around your initialization jobs:

```bash
git fetch --tags && git checkout v1.53.0
./bin/update --production check
./bin/update --production apply --stage   # back up, pin, pull, verify; restarts nothing
# the release's initialization jobs (deploy/README.md › Upgrading a production
# install): they run on the staged, verified images
./bin/update --production apply           # restart onto the release and verify it
```

A plain `apply` with nothing staged is refused before the backup. Between the
two steps the old release keeps running; do not run `docker compose up` or
`deploy/setup.sh` then. The initialization jobs migrate the database, and the
backup taken at `apply --stage` is the one to restore from: run the jobs soon
after staging, or take a fresh backup first if the old release kept writing
data in between. To abandon a staged update, roll it back.

`apply --stage` and `check` print the Compose command to run the jobs with:
it carries this install's `--env-file` and both compose files, which matters
when your env file is not `deploy/.env.production` (the docs' `$COMPOSE`
names that file). Run the jobs from a shell that does not export any key of
the env file: Compose prefers a variable exported in your shell over the env
file.

`--production` uses `deploy/.env.production` (or `--env-file PATH`) and both
production compose files. The Compose commands bin/update runs itself take
every key from that file even when your shell exports one; Compose commands
you run by hand do not. Every later command needs the same
flags (`check` prints them).

## Roll back

```bash
./bin/update rollback --yes                    # undo the last update
./bin/update rollback --receipt PATH --yes     # undo a specific one
./bin/update --production rollback --yes       # a production install (and its --env-file, if any)
```

Rollback puts back the version tags that ran before the update and checks
that the images are the ones that ran then (by digest, or by image ID for an
image built from the checkout). The previous images need the previous
release's compose files: when the checkout is a git clone, rollback compares
its deployment files with that release's tag and, if they differ, stops
before changing anything and prints the `git checkout <previous> -- <files>`
to run first (and the one that brings the newer files back before you update
again). Rolling back a staged update that never started restarts nothing. An install that followed `:latest` is pinned
to the version it actually ran; a Distributed install whose fi-collector was
built from the checkout gets that build back (`FI_COLLECTOR_VERSION` unset
again).

`rollback` without `--yes` changes nothing, except that it first recovers a
run that was killed (see below) and then stops with exit 0.

**Rollback does not revert database migrations.** If the previous version
cannot start on the migrated data, restore the backup taken before the
update. Only the most recent update can be rolled back.

## When something fails

| Exit | Meaning | What to do |
| ---- | ------- | ---------- |
| 0 | Done, or nothing to do | |
| 2 | Bad input, unreadable files or state, or a missing tool (Docker, Compose v2, Python 3.9+) | Read the message |
| 3 | Refused before an update starts: the plan blocks the update (including a signature that could not be verified, e.g. no usable cosign), no backup is configured, a production update is not staged, a readiness setting is invalid, or `rollback` needs `--yes`. Explicit metadata operations also use 3 for persistent short-lock contention | Do what the message says; `check` lists every plan reason. A control or reporting command explicitly notes any primary change already recorded before public-report contention |
| 4 | Failed before anything restarted | Nothing restarted. Usually the previous versions are configured and running again; if the run was finishing a stopped or staged update, that update is still pending and the message gives the command to finish it and the one to roll it back |
| 5 | Failed while containers were being replaced | The env file selects the new release. Inspect the stack with the command the message prints (`docker compose ps -a`, `logs`; production adds its `--env-file` and `-f` files), then run `apply` again to finish, or `rollback --receipt PATH --yes` |
| 130 | Interrupted (Ctrl-C, `SIGTERM`, `SIGHUP`) | Same as 4 or 5, depending on how far it got; the message says whether anything changed. Under `nohup`, `SIGHUP` stays ignored |

If the process is killed outright (power loss, `kill -9`), the next `apply` or
`rollback` recovers the unfinished run first: if it stopped before the restart,
it puts the version keys back as they were; if it stopped during the restart,
it records the run as interrupted, and the next `apply` of the same release
finishes it. Until then `status` shows the unfinished run and `check` plans as
if it had been recovered, provided the exact recovery record validates.
`status`, `check` (including JSON) and `apply --dry-run` use the same journal
header, installation identity and operation-recoverability checks as recovery.
Unknown/malformed journals, foreign installations and incomplete inverse
preparation block that preview with a nonzero exit, without rewriting env,
journal, receipts or retained entries. A complete armed inverse can recover;
a merely preparing inverse cannot. A matching receipt does not validate an
unknown header or authorize clearing an unfinished inverse. Do not run
production initialization jobs on the strength of an invalid recovery record:
images are only staged after an actual successful pull and digest verification.

### Env commit and private retention

Before changing the env file, journal version 2 is written durably, including
separate random operation IDs for the forward write and its exact inverse.
The inner `env_recovery` format 1 still records lexical edits, hashes, target
and mode. Recovery restores duplicate definitions, escaping, interpolation
between duplicates, comments, whitespace, BOM, CRLF, missing final newline
and unrelated bytes exactly. Parsed last-value-wins maps are not inverses.

The actual commit is **native atomic exchange**, not a destructive rename:
Linux `renameat2(RENAME_EXCHANGE)` or macOS `renameatx_np(RENAME_SWAP)`.
Each operation stages a candidate and a hard-linked identity anchor in
`<canonical-env-parent>/.futureagi-updates/env-commits/<target-id>/<operation-id>/`.
This is beside the target even for a custom production env on another volume.
A durable `manifest.json` progresses from `preparing` to `armed` before exchange.
At exchange the target becomes the complete candidate and `slot` becomes the
entry that actually occupied the target, not a pre-read backup. The updater
then verifies that displaced entry's inode, bytes, mode and ownership and the
live candidate. Only a durable `accepted` result permits Compose work. A
mismatch is a sticky `conflict`; an incomplete/uncertain record also blocks
progress. There is no automatic swap-back. Ordinary restoration is a NEW
preserving exchange, including restoration of an interrupted rollback.

Both affected directories and file data are synced; macOS also requests
`F_FULLFSYNC`, including after namespace barriers. Recovery inspects the
anchored inode identities rather than blindly exchanging again. A complete
armed operation can distinguish unexchanged and exchanged states; partial
staging/missing or contradictory evidence may require manual recovery.
An intact, demonstrably unexchanged forward operation can be durably marked
`not_applied` when recovery leaves the original file alone. A visible finished
receipt and retention index are synced again before removing a leftover
journal. A matching receipt cannot bypass an unresolved operation. The receipt is
durable before journal removal. Receipts retain schema 2, are serialized
from an explicit public field allowlist, and include only opaque commit IDs,
not private lexical data, full-file hashes, or retained contents. Private
`env-retained-<id>.json` references in the checkout state directory continue
to locate operations after the journal is cleared.

**Full env files containing secrets are now retained, even on success.** All
retention directories must be owned by the invoking user and mode 0700;
metadata files are 0600. Content inodes keep the live file's mode/owner (for
example 0640), protected by private directory traversal. Never chmod an
unknown displaced entry or follow a displaced symlink. These directories
are ignored by git, but git ignore and private permissions alone do **not**
exclude them from Docker contexts. Repository-owned build contexts (root,
backend, frontend, gateway, collector, serving, code-executor, Standalone and
test-image contexts) exclude `**/.futureagi-updates/` recursively as their
**last** Docker ignore rule. Any Dockerfile-specific `*.dockerignore` replaces
the context policy and must carry the same final rule; later public-source
negations must never re-include private state. Tests check all these policies
and inspect exported layers of minimal scratch builds with synthetic retained
before/after env files, including a stable checkout `.env` symlink.

This is not universal packaging protection: operator-defined contexts or
Dockerfile-specific ignore files outside this repository need their own final
recursive exclusion. Arbitrarily named custom env files themselves also need
an explicit exclusion; the retained-state rule is not a rule for every possible
live env filename. Exclude private state from other deployment packages,
support bundles and uploads too, and restrict any backups of it. The updater never deletes
`candidate` or `slot`, even after success: an old editor descriptor can write
the displaced inode later. There is no TTL/prune command. After 1,024 retained
operations per target, or insufficient available staging space, new writes
are refused rather than deleting potentially unique operator data.

Supported commit targets are ordinary user-owned regular files on **local
ext4 (Linux) or APFS (macOS)**, with working native exchange, hard links and
file/directory sync. User ACLs, extended attributes, special permission bits,
external hard links and unpreservable ownership are refused. macOS's automatic
`com.apple.provenance` is system-managed rather than copied; other xattrs are
refused. Private directories must also be ACL-free. NFS, SMB, FUSE, virtiofs,
9p, overlay, Windows-native and translated WSL mounts are not enabled commit
targets. There is no copy/delete or `os.replace` fallback. Successful syscalls
are not a claim that every storage controller honors power-loss barriers;
the journal volume must also provide durable file/directory sync. Stable
configured symlinks are supported, but ancestor/alias topology and the trusted
private namespace must stay stable. Observed retargets are conflicts.

The updater lock coordinates updaters, not editors. A completed in-place edit
or rename-published file at the exchange boundary is retained, and an observed
mismatch stops the run with its journal intact. **The live pathname can already
hold the updater candidate on conflict.** An open fd/mmap can continue changing
a retained inode after inspection or even after a success receipt; retention
keeps that inode named, not every historical byte immutable. Concurrent
truncation, external deletion/replacement after commit, unflushed external
data, private-state tampering by the same user/root, and arbitrary namespace
reparenting cannot be prevented by this protocol. Do not edit concurrently.

### Resolving a conflict manually

1. Stop updater invocations and quiesce all editors/writers, including old open
   descriptors. Do not rerun apply to force progress, delete the journal, or
   overwrite the live env with a guessed version map.
2. Preserve the current env, journal, private manifests/indexes, `candidate`
   and `slot` separately in an access-restricted backup. Inspect only regular
   files; do not follow a captured symlink or read devices/FIFOs. The printed
   operation directory and journal's operation IDs locate the evidence.
   `status`/`check` inspect without committing; if the env is missing/invalid,
   the CLI prints a read-only journal/retention diagnostic instead of bypassing
   ownership. No command prints retained contents.
3. Compare the separately preserved versions locally, reconcile settings and
   determine which versions actually run. Have the installation operator
   complete recovery with the original checkout/Compose provenance and backups;
   recorded conflicts are deliberately not auto-acknowledged or merged.
4. Retained names may be removed only by an explicit operation-specific manual
   action after writers are quiesced and needed data are durably archived. If
   old descriptors cannot be ruled out, retain the original inode. Deleting a
   file is not secure erasure. Never glob-prune retention to make space.

Valid legacy lexical journals acquire durable operation references before an
inverse write. Older/incomplete pre-restart journals containing only
`found_values`/`previous_values` need an independent backup, never a lossy
migration. Existing completed receipts and valid old rollout journals remain
readable. Unknown journal or sidecar formats/stages are retained for manual
diagnosis. The supported contract is preservation plus observable conflict,
not zero failure probability or writer exclusion.

`./bin/update status` shows the installed versions, an unfinished run, and the
last receipt. It works while Docker is down (the running versions then show as
unknown, and it exits 2). While another `bin/update` is running, `status` says
so instead of reporting its run as interrupted, and `check` refuses (exit 3)
until it finishes.

## Settings

Settings come from, in order: command-line flags, the process environment,
then `update.conf` in the checkout (ignored by git), then the defaults. They
never live in `.env`, which the Standalone app container reads.

```ini
# update.conf
FUTURE_AGI_UPDATE_BACKUP_COMMAND=/usr/local/bin/backup-futureagi
FUTURE_AGI_UPDATE_POLICY=minor
```

| Key | Default | What it does |
| --- | ------- | ------------ |
| `FUTURE_AGI_UPDATE_BACKUP_COMMAND` | none | Runs before an update (no shell; split like a command line). It gets `FUTURE_AGI_UPDATE_ROOT`, `_MODE`, `_PROJECT`, `_ENV_FILE`, `_FROM_VERSION` and `_TO_VERSION` in its environment. A non-zero exit stops the update. It runs without a controlling terminal (so it cannot prompt, e.g. for a sudo password); if bin/update is interrupted or the timeout passes, the hook and everything it started are stopped. |
| `FUTURE_AGI_UPDATE_BACKUP_TIMEOUT_SECONDS` | `3600` | Longest the backup may run. |
| `FUTURE_AGI_UPDATE_POLICY` | `minor` | Largest version jump `apply` makes: `patch`, `minor` or `major`. |
| `FUTURE_AGI_UPDATE_CHANNEL` | `stable` | `stable` or `beta`; a manifest for another channel is refused. Releases publish `stable` manifests only, so `beta` needs `--manifest` (or `FUTURE_AGI_UPDATE_MANIFEST_URL`) pointing at a beta manifest; none is published today. |
| `FUTURE_AGI_UPDATE_MANIFEST_URL` | the latest GitHub release's manifest | An `https://` URL or a local file. With the default, when the newest release has no manifest yet (it is attached after the release's images are published), bin/update asks the GitHub API for the newest release that has one and says so. |
| `FUTURE_AGI_UPDATE_WAIT_TIMEOUT_SECONDS` | `1200` | Distributed and production: `up --wait` timeout. Standalone uses `.env`'s `INSTALL_READY_*` settings, like `./bin/install`. |
| `FUTURE_AGI_UPDATE_TRUSTED_IDENTITY` | this repository's `release-update-manifest.yml` at `refs/tags/{version}` | The only signer accepted. `{version}` is replaced by the manifest's version. For a GitHub workflow identity like the default, the certificate must also come from a run of that repository at `refs/tags/{version}` (another repository calling the workflow is refused). Change it only for a fork that publishes its own manifests. |
| `FUTURE_AGI_UPDATE_TRUSTED_ISSUER` | `https://token.actions.githubusercontent.com` | The signer's OIDC issuer. |
| `FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY` | none | Trust a cosign public key instead of the keyless identity. |
| `FUTURE_AGI_UPDATE_COSIGN_OFFLINE` | `false` | Needs `FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY` (keyless verification needs the transparency log): verify without the transparency log, and run the cosign container without network. |
| `FUTURE_AGI_UPDATE_COSIGN_IMAGE` | pinned `cosign:v3.1.3` by digest | cosign runs from this image when no `cosign` is on `PATH`. |

### Scheduled runs

These keys control the scheduled runner. bin/update checks them every time it
loads its settings, but nothing runs unattended until you install a timer and
explicitly enable policy (with `enable --yes` or `FUTURE_AGI_UPDATE_ENABLED=true`). With the defaults (`manual` and
disabled) a scheduled run does nothing. Do not confuse
`FUTURE_AGI_UPDATE_AUTOMATION_MODE` with `FUTURE_AGI_UPDATE_MODE`: the backup
hook receives the second one as the install type (Standalone, Distributed or
production).

| Key | Default | What it does |
| --- | ------- | ------------ |
| `FUTURE_AGI_UPDATE_AUTOMATION_MODE` | `manual` | How far a scheduled run goes: `manual` (nothing), `notify` (check and report), `download` (also fetch and verify the images) or `apply` (also update, inside the maintenance window). |
| `FUTURE_AGI_UPDATE_ENABLED` | `false` | Master switch for NEW forward work. Required recovery continues even when disabled or paused. |
| `FUTURE_AGI_UPDATE_INTERVAL_SECONDS` | `21600` | Time between scheduled release checks: 300 to 86400. |
| `FUTURE_AGI_UPDATE_MAX_BACKOFF_SECONDS` | `86400` | Longest wait after repeated failures: 300 to 86400, and not less than the interval. |
| `FUTURE_AGI_UPDATE_TIMEZONE` | `UTC` | IANA time zone name of the maintenance window (for example Europe/Berlin, written exactly as in the tz database). It must exist in the host's tzdata. |
| `FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW` | none | When a scheduled apply may start. Empty means never. Otherwise `always`, or `HH:MM-HH:MM` in the time zone above (two different times; it may cross midnight). |
| `FUTURE_AGI_UPDATE_MAINTENANCE_DAYS` | `MON,TUE,WED,THU,FRI,SAT,SUN` | Days a window may start on, from `MON` to `SUN`, comma-separated, each at most once. |
| `FUTURE_AGI_UPDATE_MIN_START_BUDGET_SECONDS` | `900` | A scheduled apply starts only if at least this much of the window is left: 60 to 7200. |
| `FUTURE_AGI_UPDATE_BACKUP_MAX_AGE_SECONDS` | `3600` | Oldest backup a scheduled apply accepts: 60 to 86400. |
| `FUTURE_AGI_UPDATE_STAGE_MAX_AGE_SECONDS` | `86400` | Validated policy age bound: 300 to 604800. Scheduled runs never adopt any manual stage. The current explicit manual finish guard uses a fixed 24-hour stage and one-hour backup freshness limit. |
| `FUTURE_AGI_UPDATE_AUTOMATIC_ROLLBACK` | `false` | Legacy parsing only. This flag cannot disable mandatory snapshot restoration under `standalone-cold-restore-v1`; it grants no new image-only fallback permission. |
| `FUTURE_AGI_UPDATE_PROBE_TIMEOUT_SECONDS` | `60` | Longest a single Docker or Compose status probe may take: 5 to 300. |
| `FUTURE_AGI_UPDATE_PULL_TIMEOUT_SECONDS` | `1800` | Longest an image pull may take: 60 to 7200. |
| `FUTURE_AGI_UPDATE_TICK_TIMEOUT_SECONDS` | `14400` | Whole scheduled run bound: 900–172800. Restore-backed apply must cover pull + the forward and protected recovery budgets below. Legacy/manual budget parsing is unchanged. |
| `FUTURE_AGI_UPDATE_QUALIFICATION_POLICY` | none | Setup apply selects `standalone-cold-restore-v1`; old enrollments are not silently granted new migration/restore consent. |
| `FUTURE_AGI_UPDATE_RESTORE_TIMEOUT_SECONDS` | `3600` | Cold restoration bound: 1–86400; independent of target startup. |
| `FUTURE_AGI_UPDATE_RECOVERY_STARTUP_TIMEOUT_SECONDS` | `1200` | Previous-version readiness bound: 60–7200. |
| `FUTURE_AGI_UPDATE_STABILITY_PERIOD_SECONDS` | `60` | Health and stable restart-count interval: 1–3600. |
| `FUTURE_AGI_UPDATE_QUIESCENCE_RESERVE_SECONDS` | `300` | Per-direction stop/reconciliation reserve: 60–3600. |
| `FUTURE_AGI_UPDATE_METADATA_RESERVE_SECONDS` | `300` | Per-direction durable metadata reserve: 60–3600. |
| `FUTURE_AGI_UPDATE_ESTIMATE_SAFETY_FACTOR` | `2` | Integer safety multiplier, 2–100, on signed measured rates and growth. |
| `FUTURE_AGI_UPDATE_MIGRATION_TIMEOUT_SECONDS` | `1200` | Independent migration deadline cap: 1–86400. |
| `FUTURE_AGI_UPDATE_MIGRATION_STALL_TIMEOUT_SECONDS` | `300` | No-progress limit: 1–7200, no greater than migration timeout. |
| `FUTURE_AGI_UPDATE_DISK_WATERMARK_RESERVE_BYTES` | `1073741824` | Additional protected restore reserve: 1–9007199254740991 bytes. |

Requirements on the Docker host: Python 3.9 or newer (run isolated:
`PYTHONPATH` and user site-packages are ignored), Docker with Compose v2.24
or newer (as for `./bin/install`; v2.25.0 is refused because it cannot print
its configuration as JSON, and v2.24.1 rejects the production overlay, so use
another release), and either `cosign` v3 or newer (an older
`cosign` on `PATH` is not used) or Docker able to see the checkout (the same
requirement `./bin/install` checks).
The cosign container gets your `HTTP(S)_PROXY`/`NO_PROXY`, except a proxy on
localhost (unreachable from a container) and any kind your Docker client
config (`~/.docker/config.json` › `proxies`) already gives containers.
Windows is supported through WSL only.

## Host automation and reporting

Run the source-checkout launcher, `./bin/update`, as the installation's original
host user. Python runs isolated; the application never invokes this CLI, reads
private automation state, runs a backup, or receives a Docker socket. Manual
mode and disabled automation remain the defaults. The legacy backup-hook
FUTURE_AGI_UPDATE_MODE still means installation type, not automation policy.

### One-time automatic-update setup

Automation remains **off by default**. After a supported published Standalone installation is ready, opt in once:

```bash
./bin/update setup --mode apply
# Or explicitly consent noninteractively:
./bin/update setup --mode apply --yes --docker-context my-install-context
# During installation (only after readiness; -y alone never opts in):
./bin/install --auto-update apply
```

Setup displays the cadence, maintenance window, backup downtime/destination, Docker selection, original host
user and disable command before confirmation. Defaults are stable channel, minor-version policy, a check every
21600 seconds (six hours, with existing runner jitter/backoff), a five-minute scheduler opportunity and an apply
window of **02:00–06:00 UTC**. Use `--channel stable|beta`, `--policy patch|minor|major`, `--window HH:MM-HH:MM`
(or the explicit `always`), `--timezone IANA_ZONE`, and `--check-interval SECONDS` (minimum 300). A selected
apply mode never retains an empty window or manual policy. Setup snapshots the host-only policy once;
ambient updater exports are not a source of unattended settings. Its immutable private configuration, not
later edits to `update.conf`, supplies subsequent scheduled runs. Changing that binding is not an implicit
re-enrollment operation.

`--mode notify` checks signed releases without pulling or restarting. `--mode download` also caches approved
images without backup/recreation. `--mode manual` records disabled control without installing a scheduler.
Apply selects the restore-backed transaction: **A remains stopped continuously from its cold backup until B starts**.
B uses the normal Compose ports and unchanged restart policies, runs its migrations itself, then must pass health
checks and a restart-count stability period. A post-backup failure restores the complete original snapshot and exact
previous images automatically. The single disclosure constant is `enrollment.AUTOMATIC_UPDATE_DISCLOSURE` (A5):
setup displays it before confirmation; interactive confirmation explicitly acknowledges it, and `--yes` acknowledges
that same disclosure noninteractively. This includes discarded new-version writes, Redis/in-flight reset and irreversible
outside-service calls. It is not a zero-loss guarantee. Unknown recovery evidence stops at `recovery_required`, never a guess. The default backup destination is a private named directory directly under
HOME, outside the checkout. An explicit `--backup-destination /absolute/private/path` must also be outside all
build contexts and application bind mounts. Backups/failed-attempt evidence are retained, never pruned or mounted
into the app. Space exhaustion blocks work. Setup uses a 90-second probe timeout (the backup adapter requires at
least 70), a 3600-second backup budget and the existing 14400-second whole-tick budget; it does not change legacy
manual defaults. A backup is created for each authorized update attempt, not by a second backup schedule.

The original host user needs Python 3.9+, Compose v2, host cosign v3+, local Docker access, qualified state/storage
probes and normal scheduler authority. `--scheduler cron` (default) installs only a uniquely marked block in that
user's crontab after verifying a running daemon. `--cron-service` can select an existing supported daemon.
`--scheduler systemd --scheduler-scope system|user` installs only the named timer/service; a user manager requires
already configured persistent operation (linger). Setup never uses sudo, installs packages, changes global Docker
context/permissions, starts cron or enables linger. Missing authority/tools/probes are **incomplete**, not active.
Native Windows, Distributed (`--production`), source builds, optional profiles not qualified by the fixed observer,
remote Docker and custom mounts are refused. **The existing optional host-status overlay is not currently a
qualified automatic-backup layout**; it is not an exemption for arbitrary extra mounts. Manual/status commands
remain available. The reviewed `automatic-layout.json` is read-only shared data: setup refuses every service or
observed container using `always` or `on-failure`; only `unless-stopped` and `no` are supported, without rewriting them.
Missing layout/inspection evidence refuses. Runtime/migration observation, not an embedded format descriptor, qualifies A.

For an empty or `latest` version pin, the actual running image's OCI version label is only a lookup hint. Setup
verifies release A's signature and actual image digest, checks the authenticated physical deployment, then
normalizes only approved same-image version pins and missing local aliases. It will not overwrite a conflicting
tag, recreate an image, run migrations, or infer compatibility from consent. Unknown images refuse. Env changes
use the preserving native env-commit protocol, retaining unrelated bytes/secrets and conflict evidence. No customer
facts attestation, proof script or per-release command is needed. Future supported B/C must still carry genuine signed
compatibility evidence; setup does not certify an incompatible release or promise every future version is eligible.

For a host-controlled HTTPS publisher, `--release-source HTTPS_ORIGIN --public-key PATH --ca-file PATH` pins the
source and private copies/hashes of its public key/CA once. TLS certificate and hostname validation stay enabled.
The ordinary default remains the canonical repository and exact keyless release-workflow identity. These options
are setup-only; neither release assets nor the app can select a source, key, CA, command or path.

Setup writes disabled control first. Under runner then update locks it records the stable registration, generated
baseline, backup/capacity prerequisites and private configuration. It releases both locks before installing the
scheduler, verifies actual activation, then reacquires/revalidates and enables control **last**, with a generation
CAS. A concurrent disable/pause wins. Partial setup/activation evidence is retained; retry the identical setup after
repairing the prerequisite. Rerunning completed setup inspects the owned scheduler, preserves later baseline B/C
and never clears operator disable/pause. A configured-but-disabled/paused result exits 3, not a successful
activation. New active setup exits 0; incomplete setup exits 3. No active result is inferred from a file's existence.

```bash
./bin/update disable                     # keep schedule and private state; stop future work
./bin/update enable --yes                # requires previously completed, still-active setup
./bin/update pause --indefinite
./bin/update resume                      # clears pause only, never enables
./bin/update setup --deactivate --yes     # disable FIRST, then remove only owned scheduler
```

Disable/pause/window closure block NEW forward work, not already-required safety recovery. Deactivation disables
forward starts immediately, then returns `automatic_recovery_pending` and **keeps the safety scheduler** while an
attempt/journal is unresolved (including unreadable recovery metadata). Repeat deactivation only after terminal recovery.
The check is repeated under runner.lock before removing any owned scheduler asset. No backup or evidence is pruned.

Setup accepts explicit budget flags using the lowercase hyphenated setting names, for example:

```bash
./bin/update setup --mode apply --wait-timeout-seconds 4200 \
  --recovery-startup-timeout-seconds 4200 --migration-timeout-seconds 4200 \
  --restore-timeout-seconds 3600 --tick-timeout-seconds 43200
```

These are bounded configuration examples, not measured product qualification or a promise of a 4200-second bootstrap.
Forward = backup + migration + target startup + stability + quiescence + metadata. Recovery = restore + previous startup
+ stability + quiescence + metadata. Tick includes pull + both budgets. Remaining maintenance-window and actual
filesystem capacity preflight use signed measured rates with safety factor >=2 before A stops. Unknown measurements
stay notify-only. Releases too large for the window never start automatically outside it; the admin chooses a manual time.
Public status carries human-readable time/disk estimates and `migration_exceeds_window`, `backup_exceeds_window`,
`insufficient_disk_for_migration`, `insufficient_disk_for_backup`, or unknown-capacity reasons. Runtime `migration_stalled`
and `disk_watermark_abort` are failed forward outcomes leading to restore, not permission to retry B.

Status separates `last_run.forward_result` from `last_run.restore_result`, the effective installed version from the failed
target, the failed-target latch, `operation.phase`, snapshot/loss-window timestamps and fixed guidance IDs. While restoring
or recovery is unverified the live installed version is unknown, not the historical A/B version. The application remains a
read-only sanitized JSON consumer: no apply endpoint, host mount expansion, private paths, inventories or raw logs.

Deactivation preserves baselines, backups, receipts and unrelated jobs. The scheduler's removed ledger is terminal
for this registration: setup/enable explicitly refuse to pretend it was reinstalled. Do not delete audit records to
reset it. Use disable/pause (not teardown) when you intend a simple later enable/resume. Cron preserves unrelated
bytes, but the OS crontab API has no atomic external CAS against noncooperating editors; setup does not claim one.
The product-image A→B→C and real OS-manager/backend qualification remain separate acceptance gates; component
fixture tests alone do not establish them.

### Advanced identity-only enrollment and render-only diagnostics

These legacy commands are **not** the automatic-update setup journey:

```bash
./bin/update schedule init --docker-context my-install-context
./bin/update schedule render --backend cron
./bin/update schedule render --backend systemd
./bin/update reporting init --directory /absolute/dedicated/public-parent
```

Use the same `--production` and `--env-file` selection on every command for a
production install. Enrollment reads Compose ownership and the selected daemon
ID, records the original UID, absolute interpreter, HOME, PATH, ordered Compose
files/profiles and canonical env target, and creates private durable metadata.
The checkout, env and Compose paths must be owned/safe, not writable by other
users or symlinked. Re-initialization must match the existing binding; it never
adopts a different daemon or relocates an install. A missing tool or unknown
owner is an operator action, not permission to repair modes or change context.

Scheduled work needs host **cosign v3+** on the registered PATH. It never pulls
or starts a verifier helper container. Legacy scheduled registrations load update.conf without
ambient updater exports; one-time setup registrations use their private policy snapshot. Neither is sourced as shell. Both renderers
print reviewable assets and narrowly scoped removal instructions only. They
write no unit, crontab or timer and run no systemctl or sudo. Review and install
only the named entry under your normal OS authority. Enable changes policy
controls only: it neither installs nor starts a timer. No command changes git,
merges env settings, installs dependencies or restores databases automatically.

### Commands and exits

| Command | Effect and exit contract |
| --- | --- |
| `run` | Exactly one scheduled tick, then exit. Zero includes disabled, manual, paused, not-due and typed busy no-ops. Busy publishes nothing. Exits 2 for invalid/unavailable prerequisites, 3 for a safety refusal, 4 for a forward failure before rollout, 5 during rollout, 130 for interruption. Host-only JSON includes the actual code, transaction_started, rollout_started, forward_result and fallback_result; successful fallback never erases the forward failure. No confirmation, backup bypass, staging or force flag is accepted. |
| `download` | Explicit action even when automation is disabled. Requires schedule registration and accepts the same manifest, bundle, policy and channel selectors as check/apply. Uses the shared planner and downloader, pulls only active product images under the engine lock, and writes a separate private download receipt. Never stages, backs up, initializes or changes live env/containers. Zero means verified cached images (or typed lock busy), 2 is unavailable/invalid input, 3 a refusal, 130 interruption. |
| `enable --yes` | Persist enabled override only; 0 on success, 3 without confirmation, 2 on invalid private state. Does not change mode or pause. |
| `disable` | Persist disabled override; 0 on success, 2 on invalid private state. Ambient enabled settings cannot undo it. |
| `pause --until RFC3339` | Requires a future timestamp with explicit offset, or use `pause --indefinite`. Pauses future scheduled discovery/download/starts, not completion of an already-started safety action. Zero on success, 3 for a past/naive/invalid timestamp, including one whose UTC form falls outside the supported years 1 to 9999 (for example `9999-12-31T23:59:59-23:59`); invalid flag combinations exit 2. |
| `resume` | Clear pause only; does not enable automation or clear any failed-target latch. Zero on success, 2 on invalid private state. |
| `retry --target VERSION --manifest-hash HEX --yes` | Clear only the exact failed-target latch; retain failure/attempt evidence and the consumed window. Never apply or recover now. Zero on success, 3 without confirmation, 2 for wrong/malformed identity or incomplete attempt. |
| `schedule init --docker-context NAME` | Identity enrollment only, not baseline attestation. NAME must be non-empty and printable, without whitespace or control characters; anything else is refused with 3 (`ownership_unconfirmed`) before any Docker or supervisor work. Production may explicitly add --status-overlay (no path). Zero on success, 2 for missing tools/state, 3 for unsafe ownership/selection. |
| `schedule render --backend cron` | Render only; systemd is the other backend. Zero on success, 2 without a valid registration or executable launcher. |
| `reporting init --directory ABSOLUTE_PATH` | Explicit dedicated status-parent binding and initial unknown snapshot. Zero on success, 2 for I/O/private-state failures, 3 for unsafe, overlapping or already-bound paths or persistent short-lock contention. Registration is durable before first publication; after interruption resolve the refusal and rerun with the same directory. Existing registration is not rebound. Does not edit env/Compose or recreate containers. |
| `status` | Existing text remains default, including `--format text`. Read-only; 0 when observed, 2 when unavailable. |
| `status --format json` | Independent **host_schema_version=1** JSON: checkout, env_file, production, versions, pending_transaction, last_run and reason_codes. Host paths are private. This is NOT PublicStatusV1 and must never be served to the app. Zero when observed, 2 for unknown observations. |
| `status --export-public` | Export only to the previously registered slot, never a caller-supplied destination. Does not recover or update. Zero only after publication; 2 for publication failure or an intervening-report conflict, 3 for missing registration or persistent short-lock contention. Cannot combine with --format. Optional reports attached to manual updates still never change the engine's exit code. |
| `apply --acknowledge-stale-stage` | Host-only acknowledgment for an actual staged resume. Never accepted by run, or together with --stage/--dry-run. Absent a stage it exits 3. Other apply exit codes are unchanged. |

All commands reject flags belonging to another command (usage exit 2). Explicit
operator commands retry busy short metadata locks briefly (at most five attempts,
200 ms total waiting). If the control, state, registration or public lock stays
busy, they exit **3**, with `busy; nothing recorded; retry the command` on stderr;
never assume a silent success stopped automation. This applies to enable, disable,
pause, resume, retry, schedule init, reporting init and explicit public export.
If a control or reporting registration was already committed before its public
report encounters contention, exit 3 instead explicitly says it **was recorded**
and that nothing was recorded **in the public slot**; use `status --export-public`
to refresh the report. It does not undo a recorded disable or replay a mutation.
Other optional reporting failures warn without changing a recorded control's
success. Scheduled `run` busy remains a zero no-op, as does `download`'s existing
engine-lock busy outcome. Manual engine-lock and transaction semantics are unchanged.

Disabled, manual and paused ticks start no new forward work. Before honoring
those controls, they reconcile any retained automatic transaction under both
locks, including restore-completion metadata left after its terminal state CAS. A
registered due notify tick can check signatures and inspect the running stack;
download also caches images. Automatic apply additionally requires a qualified
unchanged-state baseline, certified v2 release, fresh attempt-bound backup proof,
unchanged identity/config, eligible maintenance window and no failed-target
latch or unfinished transaction. There is no automatic adoption of an unknown
baseline or foreign/manual transaction. An already sealed automatic attempt
instead owns recovery from its retained backup and previous images.

Long options may still be abbreviated to an unambiguous prefix, as before the
automation commands existed (`--sta` is --stage, `--ba` is --backup-command,
`--man` is --manifest), before or after the command. Abbreviation is
command-aware: an option the command does not take never takes part in matching
its prefixes, and when several options the command takes still match, the one
that predates the automation commands wins (`apply --a` is --allow-no-backup).
Full spellings are never affected. A prefix ambiguous between two older options
that the selected command takes still is ambiguous. For example, --b is ambiguous
for apply but names --bundle for check; --ba names --backend for schedule render.
Option values named after commands are still values, not command selectors.

**Production scheduled runs notify and download only.** They never stage,
initialize or apply. Production still requires explicit stage, then the
release's explicit initialization jobs, then apply. Finishing a manual stage
older than 24 hours, with a backup older than one hour, or lacking the new
freshness/proof fields requires explicit --acknowledge-stale-stage. Current
legacy backup hooks do not generate those proof fields; finishing their stages
requires acknowledgment even when recently created. This is not a new backup
or restore guarantee. Changed canonical/raw manifest, bundle, trust,
installation, config or profile bindings remain refusals even with
acknowledgment. Restore the verified original configuration before finishing;
a changed update.conf is also binding drift.

Repeating apply --stage without restarting is allowed only with a complete,
matching stored identity. It reuses the stage's backup and receipt chain,
retaining the original creation time and backup/proof timestamps and status.
Restaging may cache/verify images, but takes no second backup, creates no fresh
proof, restarts nothing and grants no automatic-apply eligibility. Missing or
unverifiable stored identity cannot be adopted by restaging. Finishing still
requires the acknowledgment above for stale or absent proof; acknowledgment
remains invalid together with --stage or --dry-run, and for scheduled run.

### Restore qualification and daemon image capacity

**Qualification remains incomplete in this development tree.** The shipping disposable rehearsal factory,
representative product witnesses/table seeders and six native product suites are not implemented end to end.
The producer therefore refuses to issue automatic eligibility; component/fake-Docker results are not product
qualification. CI must execute the real product and scheduler acceptance before a release can supply qualifying
signed evidence. The existing notify-only refusal must not be bypassed to make those jobs green.

Generated-baseline automatic apply uses authenticated contract-v2 transition evidence, the fixed
`standalone-runtime-v2` observer, `automatic-layout.json`, and per-installation A6 estimates. It does not require
customer-authored facts attestations, equal state epochs or an empty target migration plan. Expected migrations
are permitted only when the signed exact predecessor/platform/layout row positively rehearsed them and restoration.
A missing row, measurement, suite, native platform or restore certification stays notify-only. Legacy facts and
contract-v1 data remain readable for manual/notify/download use; they cannot grant restore-backed automatic apply.

Prefetch and source qualification happen while A is healthy. Post-backup guards use retained source observation and a
fresh verified cold hold, not a fictitious running A. Source/target/dependency identities, unchanged configuration and
exact original volume identities remain mandatory. Published protocol/container ports and host-address/port bindings must
match the retained normal Compose model; they are part of the observed runtime identity. The original stopped roster,
start times and restart counts are checked again before target creation can replace it. Capacity is sampled after
potentially slow final container inspections as well as before them. Concurrent operator env/config edits are never overwritten.
Restoration is authorized by the original sealed attempt, not by rediscovery or another forward-eligibility decision.
Expired freshness, pause/disable/window closure do not invalidate that genuine sealed snapshot during recovery.

There is no new automatic image-only fallback. Manual apply/stage/finish/rollback and legacy shell backup hooks keep
their existing semantics outside an active automatic transaction. A manual action encountering one refuses with
`automatic_recovery_pending`. Recovery ambiguity retains the original snapshot and bounded diagnostics; it never
silently resets a journal, starts A on B's migrated data, changes a volume identity or prunes recovery evidence.

Uncached pulls additionally require observed capacity on the **registered
image filesystem**, never the checkout disk. The built-in read-only observer
qualifies a directly connected native Linux dockerd Unix-socket peer in the
same root/mount namespace using classic overlay2, and reads that image store's
free bytes. Remote Docker, SSH contexts, macOS VM contexts, containerd snapshotters
and other drivers have unknown capacity and refuse absent-image pulls. It does
not SSH to a VM, start a helper container, change a context or guess from Docker
system-df usage. Exact already-cached images can still be independently verified.
The bounded child runner that executes every scheduled Docker command always pins
the registered context and refuses `docker context` commands, with exactly one
exception: the read-only inspection of the registered context itself, the
observer's single probe. Use, create, update, remove, list, export, import,
another name, extra words and every routing override stay refused. Real
native/VM-specific capacity qualification remains a deployment gate.

### Dedicated app projection and freshness

Enrollment assigns a random 128-bit public slot token, unrelated to private
paths or the selection hash; identical re-enrollment retains it. Reporting
creates a 0755 dedicated parent and per-install slot, with a 0644 status.json. It refuses unsafe ancestry, symlinks, preexisting foreign content,
checkout/env overlap, private-state overlap and rebinding. It never repairs
foreign permissions. Opt in separately to the Standalone or Distributed host
status Compose overlay; mount the **stable slot directory**, read-only, not the
status-file inode. The initializer prints the chosen path/overlay but does not
edit configuration or recreate anything. `bin/install --host-status-help` (also
available through `deploy/setup.sh --host-status-help`) describes the optional
read-only mount and host-only directory preparation. The private reporting binding
is written before the first public snapshot. If either write is interrupted, keep
the binding and rerun reporting init with the **same** directory after resolving
the refusal; the error prints this remedy. Never remove foreign content or change
a binding to get past a refusal. A registered slot with no status file is safe to
republish. Re-initialization preserves the latest operation and observation fields.

The app checks the enrolled slot token and independently known runtime topology.
It **does not infer the host's production identity from ENV_TYPE**: that variable
also selects application behavior on ordinary self-hosted installs. The public
production boolean remains host-reported data (and standalone with production
true remains invalid), not an app-side enrollment fact.

#### Production status-overlay identity

For a production stack already running with the status overlay, enroll explicitly:

```bash
./bin/update --production schedule init --docker-context my-install-context --status-overlay
./bin/update --production reporting init --directory /absolute/dedicated/public-parent
./bin/update --production status --format json
```

Keep the same absolute --env-file selection if it is not deploy/.env.production.
The flag is host-only, accepts **no path**, and is valid only for production
schedule init, not schedule render or any later command. It selects exactly:

1. `docker-compose.distributed.yml`
2. `deploy/docker-compose.production.yml`
3. `deploy/docker-compose.host-status.distributed.yml`

That order is recorded in compose_files and installation_fingerprint. Every
running/stopped/one-off container must prove the same exact order in its Compose
labels. Later run, download, status, check, apply and rollback derive the overlay
only from this registration; neither labels nor ambient COMPOSE_FILE can enroll
it. Without the flag, production keeps its original two-file identity. Without
registration, an overlay stack is refused with an enrollment hint, never adopted.
Standalone and non-production Distributed continue using their existing
COMPOSE_FILE selection, not this flag.

Missing, reordered, extra or changed registered files/fingerprints are typed
refusals. A missing/unsafe overlay or one absent from the deployment inventory
is refused. Enrollment checks identity, **not release trust**: verified release
checks, downloads, applies and scheduled discovery additionally require this
exact overlay entry in the signed manifest and verify deployment bytes through
the existing deployment check. An older signed manifest that omits it is not
sufficient. Status and rollback do not fetch a new manifest; their existing
observation and receipt/recovery safeguards remain in force. Production scheduled
work still only notifies/downloads; this flag never authorizes unattended apply.

**Adding or removing the overlay requires explicit re-enrollment.** There is no
in-place registration migration or force/reset flag. Disable automation, remove
only this installation's scheduler entry using the rendered removal instructions,
and wait for active work to finish. Resolve any journal or staged transaction
before changing the Compose selection. Preserve the core update journal, receipts
and env-commit evidence. Archive the exact selected private automation namespace
(as a whole, including its registration, controls, state, facts and reporting
binding) to a host-private location; do not delete individual binding fields,
other selections, or the core .futureagi-updates directory. Keep the archive for
operator reconciliation of failure latches/attempt history; enrollment is not
permission to retry a failed release.

Separately recreate the stack with the intended ordered files, then run schedule
init with the same production/env/context selection and with --status-overlay
when adding it, or without the flag when removing it. Ownership must already
match; init never recreates the stack. For first opt-in, prepare the read-only
mount using the existing two-file installation's reporting slot before archiving
its registration. That old slot can remain mounted during the transition, but is
stale/disposable, not evidence. The new registration assigns a **new random slot**:
initialize reporting in a new empty dedicated public parent, deliberately update
FI_HOST_UPDATE_STATUS_DIR and FI_HOST_UPDATE_STATUS_SLOT in the production env,
and recreate with the same three-file order to mount the new slot. Do not reuse
an old slot token or copy old facts/state to bypass identity checks. Requalify
facts if needed, validate status and the app's read-only projection, review policy
and failure history, then explicitly re-enable/reinstall scheduling. Removing
the overlay likewise requires the two-file recreation and fresh enrollment;
reporting can remain host-only without an app mount.

These are daemon-free-tested host contracts, not production-image qualification.
A real signed-release production rehearsal must still verify recreation, actual
labels, slot permissions/read-only access, atomic-replacement visibility and the
app's observation on each supported host filesystem/daemon combination.

Manual terminal outcomes, controls and scheduled ticks use the same
PublicStatusPublisher. An operation captures its private operation ticket **before
slow work**, then compares and swaps against newer operations, retaining each
returned ticket. Control changes and idle heartbeats merge into the newest private
snapshot under the short public lock; they advance the public sequence but do not
take ownership of, replace or invalidate the operation report. A manual terminal
report therefore still lands after a pause or heartbeat. A genuinely newer
operation fences the old writer: no recapture or replay over the newer report.
The fixed warning is `status_reporting_unavailable; newer operation report retained (publication conflict)`,
not a suggestion to repair registration. Busy reports retry briefly with the same
ticket and warn about public-report contention; real registration or slot failures
at setup instead say `status_reporting_unavailable; inspect host reporting registration and public slot`.
Other report-building failures say `status_reporting_unavailable; public report not recorded; inspect host status`.
Scheduled progress may skip a contended report; the terminal report retries only
`StoreBusy`, with the same opaque ticket, at most five attempts within a 200 ms
retry budget and waits of at most 50 ms. Conflicts, disk errors and interruptions
are not retried. Exhaustion emits `status_reporting_unavailable`: delivery is not
guaranteed, and neither the engine result nor engine mutation is retried.

`status --export-public` is observation-only: it never takes an operation epoch
or changes the existing operation or safety fields, even during a live backup.
It uses the captured public generation under the publication lock, so **any**
intervening report (including another export, control or heartbeat) refuses the
export with exit 2 rather than overwriting newer observations. Rerun the command
to collect fresh observations; the older export never recaptures and replays.

Scheduled lock probes create nothing, never follow a lock symlink and never
block on FIFOs. Unsafe type, owner, link or private-mode evidence is a typed
refusal, not an unlocked checkout. A genuine live engine lock means busy/exit 0
without public replacement. After discovery the runner takes the nonblocking
engine lock for its journal/history observation, so a manual run starting during
discovery cannot become a false recovery warning. Already completed discovery
accounting is retained; no engine lock is held during discovery.

A publisher can repair a 0700 public parent/slot only when its private provenance
matches that directory's exact parent and directory inode, or the current call
observed creation and durably bound the inode before chmod. A crash between mkdir
and that binding is deliberately **not** auto-repaired; a pre-mkdir intent and an
empty directory do not prove ownership. On this refusal, inspect the path and
private provenance on the host. Only after independently confirming the directory
is the intended dedicated public directory may its owner explicitly set it to
0755 and retry the same reporting binding. Do not chmod, remove or replace foreign
directories or their contents to bypass the refusal. Known inode-bound
interruptions before chmod remain automatically recoverable.

Public outcomes follow the returned engine receipt, not merely exit 0.
`apply --dry-run` and an up-to-date apply report a check, not an applied update.
A returned staged receipt reports `staged`, or `awaiting_initialization` with
`initialize_then_apply` on production. Only a completed apply receipt reports
apply succeeded; a completed rollback reports rolled_back. Read-only checks retain
precedence rules that preserve a live or failed apply. No new engine journal stage
or Docker command is introduced by reporting.

Public status is an allowlisted, disposable projection, never an authorization,
backup, recovery, download or release-proof input. No env values, paths, raw
exceptions, commands or secrets go into it. Atomic replacement and directory sync
preserve old-or-new reads. Optional publication failure is nonfatal to the manual
update outcome and does not imply an update failed or should be retried. An
explicit export is itself a reporting operation and returns nonzero on failure.

Heartbeat time, version observation, verified discovery, download inspection and
operation-progress timestamps are independent. A control change or idle tick
does not refresh old observations. Failed discovery retains the last verified
candidate and its original as-of time; a new check without reinspection can
invalidate a previous downloaded claim. Discovery backoff and the independently
due action/window opportunity are durable; missed timer wakes do not form a
catch-up queue. A failed target remains quarantined until explicit retry.

## Test prerequisites

Run the daemon-free updater suite from the repository root with Python 3.9 or
newer. Positive host-ownership and public-reporting fixtures exercise the real
path guards: their temporary directories need trusted ancestry, not merely a
0700 child of world-writable `/tmp` (including sticky `/tmp`). Use a private,
owned temporary directory beneath a trusted `$HOME`, as Update CI does:

```bash
(
  set -euo pipefail
  TMPDIR="$(mktemp -d "$HOME/fagi-update-tests.XXXXXX")"
  export TMPDIR
  trap 'rm -rf -- "$TMPDIR"' EXIT
  python3 -m unittest discover -s deploy/tests -p 'test_update_*.py' -v
)
```

Every ancestor of that home must be a real directory owned by the current user
or root and not group/world-writable. If your home does not meet that prerequisite,
choose another operator-owned trusted parent; do not chmod shared directories or
relax the runtime guards. Native env-file exchange/recovery tests also require
ext4-backed temp space on Linux or APFS on macOS: a container's overlayfs or a
virtiofs bind mount is not equivalent. Tests that intentionally introduce unsafe
ancestry still must refuse it. Install optional `jsonschema==4.23.0` to exercise the schema
checks (CI does so on Python 3.12); release/image-convention tests additionally use
`PyYAML==6.0.3`. Daemon-free Compose rendering needs a working `docker compose`.
Real Docker E2E suites remain separately opt-in; this command does not start them.

## The release manifest

`release-manifest.schema.json` describes it; `manifest.py` enforces it,
including the rules a schema cannot express. Each release's
`.github/workflows/release-update-manifest.yml` run (called by
`release-images.yml` once every image and the chart are published) resolves
every image digest, cross-checks them against the Helm chart's images lock,
hashes the tag's deployment files, signs the manifest and attaches it to the
release. A published manifest is never replaced.

A deployment file's digest is what `sha256sum FILE` prints; a directory's is
a tagged sha256 over its files' relative paths and digests, leaving out, at
any depth, `__pycache__`, `.pyc`/`.pyo` files, every name that starts with
`.`, editor backups (`name~`), autosaves (`#name#`) and `Thumbs.db` /
`desktop.ini` in any case (`hash_path` in `manifest.py`; the release
generator refuses to leave out a file git tracks). `check` names the file or
directory that differs.

The manifest decides versions and digests, never repository paths: every
image is pulled from the repository the install's own compose files name.
Releases are published to Docker Hub (`docker.io/futureagi/*`), and the plan
refuses an install that pulls from another registry: registry mirrors are not
supported by `bin/update` yet, so update such an install by hand
(`deploy/README.md` › Upgrades).
