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

These keys control scheduled runs. One-time setup reads them once (its flags
first, then `update.conf`; exported variables are ignored) and keeps the result
as private configuration, so later edits to `update.conf` do not reach an
installation set up that way. A legacy `schedule init` registration instead reads
`update.conf` (never exported variables) on every run. With the defaults
(`manual` and disabled) a scheduled run does nothing. Do not confuse
`FUTURE_AGI_UPDATE_AUTOMATION_MODE` with `FUTURE_AGI_UPDATE_MODE`: the backup
hook receives the second one as the install type (Standalone, Distributed or
production).

| Key | Default | What it does |
| --- | ------- | ------------ |
| `FUTURE_AGI_UPDATE_AUTOMATION_MODE` | `manual` | How far a scheduled run goes: `manual` (nothing), `notify` (check and report), `download` (also fetch and verify the images) or `apply` (also update, inside the maintenance window). |
| `FUTURE_AGI_UPDATE_ENABLED` | `false` | Master switch for NEW forward work. Setup enables it through the private control only after the scheduler is verified. Required recovery continues even when disabled or paused. |
| `FUTURE_AGI_UPDATE_INTERVAL_SECONDS` | `21600` | Time between scheduled release checks: 300 to 86400. |
| `FUTURE_AGI_UPDATE_MAX_BACKOFF_SECONDS` | `86400` | Longest wait after repeated failures: 300 to 86400, and not less than the interval. |
| `FUTURE_AGI_UPDATE_TIMEZONE` | `UTC` | IANA time zone name of the maintenance window (for example Europe/Berlin, written exactly as in the tz database). It must exist in the host's tzdata. |
| `FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW` | none | When a scheduled apply may start. Empty means never. Otherwise `always`, or `HH:MM-HH:MM` in the time zone above (two different times; it may cross midnight). `setup --mode apply` records `02:00-06:00` unless you pass `--window`. |
| `FUTURE_AGI_UPDATE_MAINTENANCE_DAYS` | `MON,TUE,WED,THU,FRI,SAT,SUN` | Days a window may start on, from `MON` to `SUN`, comma-separated, each at most once. |
| `FUTURE_AGI_UPDATE_MIN_START_BUDGET_SECONDS` | `900` | A scheduled apply starts only if at least this much of the window is left: 60 to 7200. |
| `FUTURE_AGI_UPDATE_BACKUP_MAX_AGE_SECONDS` | `3600` | Oldest backup a scheduled apply accepts: 60 to 86400. |
| `FUTURE_AGI_UPDATE_STAGE_MAX_AGE_SECONDS` | `86400` | Validated policy age bound: 300 to 604800. Scheduled runs never adopt any manual stage. The current explicit manual finish guard uses a fixed 24-hour stage and one-hour backup freshness limit. |
| `FUTURE_AGI_UPDATE_AUTOMATIC_ROLLBACK` | `false` | Legacy parsing only. This flag cannot disable mandatory snapshot restoration under `standalone-cold-restore-v1`; it grants no new image-only fallback permission. |
| `FUTURE_AGI_UPDATE_PROBE_TIMEOUT_SECONDS` | `60` | Longest a single Docker or Compose status probe may take: 5 to 300. |
| `FUTURE_AGI_UPDATE_PULL_TIMEOUT_SECONDS` | `1800` | Longest an image pull may take: 60 to 7200. |
| `FUTURE_AGI_UPDATE_TICK_TIMEOUT_SECONDS` | `14400` | Whole scheduled run bound: 900–172800. Restore-backed apply must cover pull + the forward and protected recovery budgets below. Legacy/manual budget parsing is unchanged. |
| `FUTURE_AGI_UPDATE_QUALIFICATION_POLICY` | none | `setup --mode apply` records `standalone-cold-restore-v1`, which selects the restore-backed transaction and the budgets below. An enrollment without it is never granted restore-backed automatic apply; setup does not add it to an older enrollment. |
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

## Automatic updates (Standalone)

Automatic updates are **off** unless you opt in once. After that, a scheduler
on the Docker host runs `./bin/update run` every five minutes. The runner checks
for a newer signed release every six hours by default and, when a release
qualifies for this installation, applies it inside your maintenance window,
database migrations included, without any further command. A release that does
not qualify is reported and left for you to apply by hand. The application
never starts, approves or retries an update: everything runs on the host, as
the user who opted in.

### Phase 1 status

**No published release qualifies for automatic apply yet.** A release applies
automatically only when its signed manifest carries a compatibility proof with a
row for your exact installed version, topology, profiles, platform and
deployment files, produced by a real upgrade-and-restore rehearsal. The exact-pair
executor must complete all six suites, retain genuine measurements and verify
cleanup before producing that proof. Its complete native acceptance is still
under validation; the passing bounded milestones are not release proofs.
Releases published before this feature carry no proof. Until a qualified release
is published, an opted-in installation checks, verifies and reports new releases,
and every release stays notify-only with a reason such as `uncertified_release`.

### Supported installations

Setup accepts only a **Standalone** install (`docker-compose.yml`) on a host
that is not native Windows; Distributed, production (`--production`) and Helm
installs are refused, and `bin/install.ps1` has no automatic-update option.
Setup runs the same read-only state probes in every mode, so these apply to
`notify`, `download` and `apply` alike:

- **Published images.** Images built from the checkout (`--from-source`) are
  refused; the running images' registry digests must match the signed manifest
  of the release they report.
- **The shipped layout.** No optional profile (`ml`, `sandbox`), no build
  section, and no extra service, named volume or mount: every one must match
  `deploy/update/automatic-layout.json`. The optional host-status overlay adds a
  mount that layout does not cover, so an install that uses it is refused with
  `unsupported_installation`.
- **Restart policies `unless-stopped` or `no`.** The shipped file uses
  `unless-stopped`; `always` and `on-failure` are refused, never rewritten. Apply
  also inspects each running container's actual policy.
- **Host tools for the original host user:** Python 3.9 or newer, Docker Compose
  v2, host `cosign` v3 or newer on `PATH`, and a running cron daemon or a usable
  systemd manager. Setup never installs any of them.

`download` and `apply` also need **measurable Docker storage**: a local Linux
dockerd on its Unix socket using the overlay2 driver, or Docker's containerd
image store at its default paths. Docker Desktop, other VM-backed contexts and
remote contexts report unknown capacity and are refused. Only `apply` needs a
backup directory.

### One-time automatic-update setup

**From the installer.** An interactive `./bin/install` of a published
Standalone install asks once, after stack readiness and the first-account step,
and only when this checkout has no automation state yet. It prints a
four-line summary (six-hour checks, stable/minor releases, a 02:00-06:00 UTC
window, cold-backup downtime, backups kept under your home directory, the tools
it needs, `./bin/update disable` to stop) and asks
`Opt in once to supported automatic updates? [y/N]`. Answering `y` runs
`./bin/update setup --mode apply --yes`: your answer is the consent and setup
does not ask again; it prints the full disclosure below as it starts. **To read
the disclosure before agreeing, answer `N` and run `./bin/update setup --mode
apply` yourself.**

`./bin/install --auto-update apply` (or `notify`, `download`) opts in without
the prompt, also together with `-y`; `-y` alone never opts in. `--auto-update`
is refused with `--no-up`, `--from-source` and `--distributed`. When setup does
not complete, the installer still finishes, warns that automatic-update setup is
incomplete and exits 3 (1 still wins when the first account could not be
created).

**From an existing installation:**

```bash
./bin/update setup --mode apply          # prints the summary and the disclosure, then asks
./bin/update setup --mode apply --yes    # unattended: --yes acknowledges the same disclosure
```

The interactive question for `--mode apply` is
`Acknowledge the disclosure and authorize automatic migration updates with mandatory restore? [y/N]`
(other modes ask `Authorize this one-time host action? [y/N]`). Without a
terminal and without `--yes`, setup declines, changes nothing and exits 3.

**The disclosure.** Setup prints this text for `--mode apply` before it asks,
and `--yes` acknowledges exactly this text. Its single copy is
`AUTOMATIC_UPDATE_DISCLOSURE` in `deploy/update/enrollment.py`:

> Automatic updates stop Future AGI, take a cold backup, and start the new
> version, which may migrate the database. Future AGI is unavailable while this
> happens (usually several minutes, longer for large databases). If the new
> version doesn't pass its health checks, the updater restores that backup and
> the previous version automatically. Anything written to the new version before
> a failed update is rolled back is discarded, in-flight jobs and the Redis cache
> are reset, and calls already made to outside services (webhooks, model
> providers) can't be undone. Some failures still need manual recovery: the
> updater stops and says so instead of guessing.

The summary around it also states the mode, channel, version policy, check
cadence, window and time zone, the forward and protected recovery budgets (6660
and 5460 seconds with the defaults), the backup directory, the Docker target,
the scheduler and the host user, and the commands that disable or remove
automation.

**Choices**, fixed at setup:

- `--mode`: `apply` (check, prefetch, back up and update inside the window),
  `download` (check and cache verified images; never stops anything), `notify`
  (check and report only) or `manual` (record disabled control; install no new
  scheduler, and keep any existing owned scheduler).
- `--channel stable` or `beta` (default `stable`); `--policy patch`, `minor` or
  `major` (default `minor`): the largest version jump taken.
- `--window HH:MM-HH:MM` or `--window always`, in `--timezone IANA_ZONE`
  (default UTC). Apply always gets a window: `02:00-06:00` unless you pass one
  (a window set in `update.conf` is not used). Start days come from
  `FUTURE_AGI_UPDATE_MAINTENANCE_DAYS` (every day by default).
- `--check-interval SECONDS`: 300 to 86400 (default 21600, six hours), plus up
  to 10% jitter; failed checks back off up to
  `FUTURE_AGI_UPDATE_MAX_BACKOFF_SECONDS`.
- `--backup-destination /absolute/path` (apply only). The default is a private
  directory named `~/.futureagi-backups-<id>` in your home directory. It must be
  outside the checkout, every build context and every bind mount, and is created
  owner-only (0700) on local ext4 or APFS.
- `--docker-context NAME`: the context to record (default: the current one,
  read once). A `DOCKER_HOST` or `DOCKER_CONFIG` in your shell is refused rather
  than recorded.
- `--scheduler cron` (default: a marked block in your own crontab, after
  checking that a cron daemon runs; `--cron-service` names which) or
  `--scheduler systemd` with `--scheduler-scope system` or `user` (a user
  manager must already have lingering enabled).
- `--release-source HTTPS_ORIGIN --public-key PATH --ca-file PATH` pins a
  host-controlled HTTPS publisher and private copies of its key and CA once.
  Certificate and hostname checks stay on. The default is this repository's
  GitHub releases and the keyless identity of `release-update-manifest.yml`.
- Budget flags named after the settings in lowercase with hyphens
  (`--backup-timeout-seconds`, `--wait-timeout-seconds`,
  `--migration-timeout-seconds`, ...), see [Budgets](#budgets).

Setup reads its inputs once: flags first, then `update.conf` as it is at that
moment, then the defaults. Exported `FUTURE_AGI_UPDATE_*` variables are ignored.
It forces a 90-second probe timeout and no backup hook, and stores the result as
private configuration that every later scheduled run uses. Editing `update.conf`
afterwards changes nothing, and repeating setup with different choices is
refused (`configuration_changed`); Phase 1 has no command that changes them
after setup.

**What setup does,** in order. It writes disabled control first. It proves that
this checkout owns the running Compose project and records the Docker context,
daemon ID, host user, interpreter, home directory and `PATH`. For apply it
checks the layout and restart policies. It identifies the running release from
the images' version label, verifies that release's signed manifest, checks that
every running image has the signed digest and that the checkout's deployment
files match the release, and runs the fixed read-only state probes against the
running stack. Empty or `latest` version keys are pinned to the verified version
of the same images (a missing local tag is added to the same image ID) through
the preserving env-file commit; nothing is recreated or restarted. For download
and apply it measures free space in Docker's image store; for apply it creates
the backup directory, checks it has room for the protected restore reserve and
records the backup binding. It then records a baseline of the installation,
installs the scheduler, verifies that the scheduler is really active,
revalidates everything and enables control **last**. A disable or pause that
happens meanwhile wins.

Setup never runs migrations, recreates containers, uses sudo, installs packages,
starts cron, enables lingering or changes the global Docker context.

**Results.** `Setup active` exits 0, followed by the next apply window and the
verified Docker target. `Setup incomplete: <reason>` exits 3: nothing unattended
was activated; fix what the reason names and run the identical command again
(partial progress is kept and reused). `configured` exits 3 too: an earlier
`disable` or `pause` is kept, because setup never re-enables. Rerunning a
completed setup only inspects the scheduler. `--mode manual` exits 0.

**After setup, leave the checkout as it is.** Automatic updates run from the
checkout, `.env` and Compose files as setup recorded them and change only the
version keys. Editing any line of `.env`, a `git pull` or `git checkout` that
changes the Compose or bootstrap files, or applying a release with a manual
`./bin/update apply` makes later automatic checks refuse with
`configuration_changed` or `deployment_files_mismatch`. Phase 1 has no command
that records a new baseline after such a change.

### What happens on each scheduled check

The scheduler runs `./bin/update run`, which performs one tick and exits; its
output is discarded. Each tick:

1. Takes the runner lock. If a manual `bin/update` command is running, the tick
   does nothing.
2. **Finishes unfinished work first.** If an earlier automatic attempt was
   interrupted (killed process, reboot, Docker restart), the tick resumes it
   from its durable record, either finishing an accepted success or completing a
   restore, before anything else, even when automation is disabled or paused.
3. Stops if automation is disabled, paused or `manual`.
4. Stops unless a release check is due, or a release found earlier is waiting
   for its window.
5. **Discovers** the newest release the channel and version policy allow from
   the GitHub release's `future-agi-update-manifest.json`, and verifies its
   Sigstore signature with host cosign: it must have been signed by this
   repository's `release-update-manifest.yml` at that release's tag.
6. Refuses while a manual update is staged or unfinished.
7. In `notify` mode it records the release as available (or the install as
   current) and stops.
8. **Qualifies the release for this installation.** The signed compatibility
   proof must have a row for the exact installed version, Standalone, no
   profiles, this platform and these deployment files, rehearsed together with
   restoration. The running stack must still match the recorded baseline: same
   configuration, `.env`, deployment files and images. Otherwise the release
   stays notify-only and the tick records the reason.
9. In `download` mode it pulls and verifies the images and stops.
10. In `apply` mode it waits for the maintenance window: at most one attempt per
    window occurrence, starting only with at least
    `FUTURE_AGI_UPDATE_MIN_START_BUDGET_SECONDS` of the window left, and never
    for a release whose earlier attempt failed (`failed_target_latched`).
11. **Prefetches** the new images and verifies their digests while the running
    version keeps serving.
12. **Checks capacity and time (A6)** while the running version is still
    healthy: it measures the tables the release's migrations touch, the three
    data volumes and the free space where the backup and Docker's data live, and
    estimates backup, migration and restore from the proof's measured rates (see
    [Releases that stay notify-only](#releases-that-stay-notify-only)). If the
    update does not fit, the release stays notify-only for this installation and
    the estimates are reported.
13. Runs the update transaction.

### The update transaction

Below, A is the running version and B the release being applied.

1. Reserves the attempt and its private recovery ledger in the backup directory.
2. **Stops A** and verifies that every container is stopped.
3. **Takes a cold backup** of the `postgres-data`, `clickhouse-data` and
   `app-data` volumes (archives with a hash inventory, written by a
   network-less helper from the installed Postgres image) plus the private
   configuration file the app mounts, then seals it. **From here on A stays
   stopped** until B passes its checks or A is restored.
4. Checks the cold hold again: the same containers, still stopped, with the
   same restart counts.
5. Writes B's version keys to `.env` (only those values change; the rest of the
   file stays byte for byte).
6. **Starts B directly** from the normal Compose model: same ports, same
   restart policies, B's verified images, A's volumes. B runs its database
   migrations itself while it boots. Until its boot finishes, the app answers
   with a 503 "starting" page and accepts no writes.
7. While B boots, three guards run: a migration deadline (the estimate times the
   safety factor, capped by `FUTURE_AGI_UPDATE_MIGRATION_TIMEOUT_SECONDS`), a
   no-progress limit (`migration_stalled` when neither the boot phase, the
   current migration nor PostgreSQL's progress views move for
   `FUTURE_AGI_UPDATE_MIGRATION_STALL_TIMEOUT_SECONDS`) and a disk watermark
   (`disk_watermark_abort` as soon as free space falls below the protected
   restore reserve).
8. **Decides success** only after the health checks pass and stay passing for
   `FUTURE_AGI_UPDATE_STABILITY_PERIOD_SECONDS` without any B container
   restarting, and after B's images, configuration and storage match what was
   expected. It then records B as the new baseline and writes a receipt.

A is stopped at step 2 and remains unavailable until B boots successfully or A
is restored. B may begin serving before the stability period in step 8 ends; if
B then fails before the durable success decision, the restore discards everything
B accepted after its API first answered. A failure before the backup is sealed
is reconciled by aborting
quiescence, not by restoring a snapshot. Once success is durably decided, recovery
finishes B's metadata instead of restoring A.

### When the new version fails: automatic restore

If a failure, interruption or guard refusal occurs **after the backup is sealed
and before success is durably decided**, the updater records the decision to
restore and then, from that sealed backup:

1. stops B's containers, checking each one's exact identity;
2. restores the three volumes in place from the archives, verifying every hash;
3. puts back the version keys of `.env` exactly as they were;
4. starts A's exact previous images and waits for health and the stability
   period;
5. records `restored_after_update_failure` and quarantines the failed release
   (`failed_target_latched`), so it is not attempted again until you clear it.

The restore has its own protected time budget, which the forward attempt can
never spend. Once decided, it is not stopped by the window closing, a pause, a
disable or the backup's age. The tick reports the disposition `restored`.

**What a restore loses.** Everything B wrote: data it accepted between its API
first answering and the failure decision, and its own migration and bootstrap
writes. In-flight jobs and the Redis cache are reset, and calls B already made
to outside services (webhooks, model providers) cannot be undone. The sealed
snapshot preserves A's persistent state at quiescence; A stays stopped while it
is taken. This is not a guarantee against every kind of data loss. The status
report gives the backup time as `snapshot_at` and as the start of the loss
window (`loss_window_qualification` is `conservative`).

To try the same release again, clear its quarantine:

```bash
./bin/update retry --target v1.55.0 --manifest-hash <hash> --yes
```

Use the failed attempt's retained identity for `retry`: its private attempt
record holds the exact target version and manifest hash. Cross-check the attempt
with `./bin/update status --format json` and the matching backup evidence.
`./bin/update check --format json` fetches the
currently configured manifest and may show a newer release, not the target that
failed. `retry` only clears the exact quarantine: that release is attempted
again at a later window if it still qualifies; the window already used stays
used, and the failure evidence is kept.

Backups and failed-attempt evidence are never deleted. Each attempt adds one
full cold backup to the backup directory, and Phase 1 has no pruning command.
Do not delete attempt directories by hand: the updater rereads the evidence of
restored attempts. When the directory fills up, releases stay notify-only with
`insufficient_disk_for_backup`.

### Releases that stay notify-only

A release that does not qualify is never applied automatically. The tick
records why, and the release stays available to apply by hand (with your own
backup, see [Update a Standalone or Distributed install](#update-a-standalone-or-distributed-install);
afterwards automatic updates refuse with `configuration_changed`, see above).
The codes below cover release discovery, proof selection and scheduled-apply
preflight. They distinguish a release that cannot be verified from one that is
verified but not qualified for this particular installation.

| Reason | What it means |
| --- | --- |
| `manifest_unavailable`, `network_unavailable`, `rate_limited` | The release check could not fetch a complete manifest or proof. Fix connectivity or retry after the publisher's rate limit. |
| `release_incomplete`, `discovery_incomplete` | The release is missing its signed manifest or bundle, or the updater could not complete discovery; it will not silently treat an incomplete check as current. |
| `signature_rejected`, `host_verifier_required`, `unsupported_contract`, `immutable_release_changed` | The signature, required host verifier, manifest contract or immutable release bytes could not be trusted. Do not bypass verification. |
| `channel_mismatch`, `version_policy_blocked` | The release is on another channel, or its version jump exceeds the configured policy. |
| `uncertified_release`, `restore_not_certified`, `restore_procedure_unsupported`, `backup_inventory_mismatch` | No usable signed proof certifies the exact restore procedure and complete persistent-data coverage, or the setup predates restore-backed authorization. This includes every release today; see [Phase 1 status](#phase-1-status). |
| `transition_not_rehearsed` | The proof has no row for the exact installed version, topology and profiles. |
| `platform_not_rehearsed` | The exact transition was not rehearsed on this host's platform (for example arm64). |
| `deployment_files_mismatch` | The checkout's Compose or bootstrap files differ from the authenticated retained deployment or the enrolled baseline. |
| `unsupported_installation`, `ownership_unconfirmed` | The topology, profiles, restart policy, mount/volume layout or Docker-project ownership is outside the supported contract or cannot be proved. |
| `required_env_missing` | The install's env file lacks a setting required by the candidate release. |
| `configuration_changed` | `.env`, the Compose configuration, the running images or the recorded setup changed since setup or the last automatic update. |
| `image_digest_mismatch` | A running or prefetched image does not match the digest authenticated by the signed manifest. |
| `running_versions_unknown`, `mixed_running_versions` | The updater cannot establish one coherent version for the running app images. |
| `migration_inventory_unsupported`, `migration_inventory_mismatch` | The applied migrations are not in a layout the fixed probe supports, or differ from the authenticated transition. |
| `manual_stage_present`, `recovery_required`, `automatic_recovery_pending` | A manual stage or unfinished/ambiguous transaction needs to be resolved before a new release can start. |
| `scheduler_unregistered`, `control_invalid` | The owned scheduler registration or private enable/pause controls are missing or invalid. Repair the named host state; do not invent a registration. |
| `outside_window`, `insufficient_window`, `clock_skew`, `timezone_unavailable` | The configured maintenance window is closed, too little eligible time remains, the host clock conflicts with recorded observations, or the configured time zone is unavailable. |
| `failed_target_latched` | An earlier attempt at this exact release failed and was restored; clear its quarantine with `retry` only after review. |
| `backup_exceeds_window` | The measured backup or restore exceeds its configured timeout, or backup, startup/stability and protected recovery do not fit the remaining time. |
| `migration_exceeds_window` | The measured migration exceeds `FUTURE_AGI_UPDATE_MIGRATION_TIMEOUT_SECONDS`, or the whole forward and protected recovery budgets do not fit. |
| `restore_capacity_unknown` | Required size, filesystem or free-space data is missing/unsupported, a touched table is larger in rows or bytes than its rehearsal reference, or measurements are otherwise ambiguous. |
| `restore_capacity_insufficient` | A data-volume filesystem lacks the required free-inode reserve for restoration. |
| `insufficient_disk_for_backup` | The backup destination or Docker data filesystem cannot hold the measured backup and protected restore reserve. |
| `insufficient_disk_for_migration` | Docker's data filesystem cannot also hold the migration's measured growth, WAL and temporary-space reserve. |
| `disk_space_insufficient` | The image store cannot safely accommodate the required image download. |

`outside_window` and `insufficient_window` are scheduling outcomes; the release
may be reconsidered at a later eligible occurrence. A6 admission refusals are
installation-specific, not permanent release failures. The public status report
includes the estimates that explain them. After the transaction begins, other
operational outcomes can include `migration_stalled`, `disk_watermark_abort`,
`readiness_failed`, `operation_timeout`, `operation_interrupted`,
`backup_failed`, `restore_verification_failed`, `restored_after_update_failure`
and `status_reporting_unavailable`; these are not proof-qualification reasons.
A failed target is quarantined after restoration, while ambiguous recovery stays
`recovery_required` and requires operator attention.

How the time and disk estimates (A6) work:

- Measurements come only from the signed proof row for your exact transition.
  Backup and restore throughput are measured in every rehearsal. Migration
  evidence is either an exact per-migration measurement list or the explicitly
  tagged `normal-bootstrap-envelope-v1` record: a measured whole normal target
  start and readiness interval, with its exact ordered pending plan and table
  scope. The envelope is not an invented per-migration rate; admission charges
  its whole duration and observed disk growth, WAL and temporary-space peaks.
  Resource samples are bounded observations, not a guarantee that every spike
  between samples was captured.
- Only registered high-volume tables touched by the pending migrations are
  seeded to reference scale. Other migrations still execute within the measured
  normal bootstrap. The producer compares the reference table mapping with an
  independent pre-start target-image loader probe; an omitted mapping is not an
  empty plan. Repeated use of the same reference table across migrations remains
  refused until intermediate-size bounds are supported.
- Your sizes come from the running installation: the row counts and bytes of the
  tables the migrations touch, the three volumes' sizes and the free bytes of the
  backup directory and of Docker's data filesystem. A touched table with more
  rows or bytes than the rehearsal's reference is not extrapolated: the release
  stays notify-only (`restore_capacity_unknown`).
- Every duration and growth figure is multiplied by
  `FUTURE_AGI_UPDATE_ESTIMATE_SAFETY_FACTOR` (at least 2) and rounded up. Missing
  data is never treated as zero.
- The whole forward attempt plus the protected recovery must fit the smaller of
  the window time left and the tick time left. A release too large for the window
  is never started outside it; apply it by hand at a time you choose.

When the host status report is set up (see
[Status on the host and in the app](#status-on-the-host-and-in-the-app)), it
carries the estimates as whole seconds and bytes: `backup_seconds`,
`migration_seconds`, `restore_seconds`, `extra_disk_bytes`,
`backup_required_free_bytes`, `docker_required_free_bytes`,
`protected_restore_reserve_bytes`, `forward_seconds`, `recovery_seconds`,
`total_seconds`, `migration_deadline_seconds` and
`migration_stall_timeout_seconds`. The app's Updates page shows them as readable
times and sizes.

### Pause, disable and deactivate

```bash
./bin/update disable                      # stop new work; keep the scheduler and private state
./bin/update enable --yes                 # turn it back on; needs a completed setup whose scheduler is active
./bin/update pause --until 2026-12-01T00:00:00+00:00
./bin/update pause --indefinite
./bin/update resume                       # clears a pause only; never enables, never clears a quarantine
./bin/update setup --deactivate --yes     # disable first, then remove only the owned scheduler
```

Disable, pause and a closed window stop **new** work only. An automatic attempt
that already started still finishes its restore or its success, on the next
ticks if needed. Deactivation disables first; while an attempt is unresolved
(or its recovery metadata cannot be read) it keeps the scheduler, exits 3 with
`automatic_recovery_pending`, and you repeat it after recovery has finished.
Deactivation keeps backups, baselines, receipts and unrelated crontab lines. It
is final for this registration: setup and `enable` refuse to reinstall a removed
scheduler. Use `disable` or `pause` when you mean to turn automation back on
later.

Every control exits 0 when recorded, 3 when its confirmation is missing or a
short metadata lock stays busy (`busy; nothing recorded; retry the command`),
and 2 when the private state is invalid. A manual `bin/update apply`, `stage`,
or `rollback` started during an automatic attempt is refused with
`automatic_recovery_pending`.

### When status says recovery_required

`recovery_required` (guidance `review_recovery`) means the updater could not
safely finish or classify a retained transaction. It is **not** an instruction
to restore the snapshot: the correct action depends on the authenticated
attempt evidence. The installed version is reported as unknown, and Future AGI
may stay down while recovery is unresolved.

When retained authority is available, each tick reopens it before other work
and retries the matching recovery branch with a fresh recovery-only budget, even
while automation is disabled or paused. Missing or unreadable authority cannot
select a recovery branch and remains blocked for operator review:

- **No durable backup seal yet:** it reconciles the interrupted quiescence and
  may start A only after proving the original container identities. There is no
  sealed snapshot to restore.
- **A durable success decision exists:** B has already been accepted. Recovery
  only finalizes B's receipt and metadata; it must never restore A's snapshot.
- **A backup is sealed and success was not decided:** recovery is directed to
  restore A from that sealed backup. A temporary host problem can leave this
  restore incomplete and eligible for another safe retry.
- **The retained records or runtime identity cannot be authenticated, or
  restored data cannot be verified:** the updater stops instead of guessing.
  Do not infer the recovery direction from the displayed version alone.

What to do:

1. **Do not mutate the installation.** Do not run `docker compose up` or `down`,
   `./bin/install`, manual `./bin/update apply` or `rollback`; do not start, stop,
   remove or recreate containers; and do not delete or replace volumes, the
   `.futureagi-updates/` directory, attempt evidence or anything in the backup
   directory.
2. **Inspect without changing state:** run `./bin/update status` and
   `./bin/update status --format json`, then `docker compose ps -a` and
   `docker compose logs app`. Preserve the attempt's directory in the backup
   destination (named after its attempt ID), including the recovery ledger,
   proof, hash inventory and bounded logs. The private attempt record and ledger
   identify the target and the durable recovery direction; public status alone
   may not expose every detail.
3. **Fix only an independently confirmed transient host problem, then let
   retained recovery retry.** For example, a host check may confirm that Docker
   is stopped or that its filesystem has exhausted free bytes or inodes. Recovery
   failures can be reported only as generic `recovery_required`; normal status
   and the ledger do not necessarily retain the underlying capacity, permission
   or integrity error. Do not infer a cause from that generic code. Ask for
   support with the retained evidence when the cause is unclear. If capacity is
   confirmed as the cause, add capacity or remove unrelated disposable data,
   never the installation's volumes or retained backups. A prior restore pass
   may already have restored some volumes; preserve its per-volume phases.
   After the confirmed host problem is resolved, wait for the next tick or run
   `./bin/update run` once to invoke the same recovery path.
4. **Do not force a restore when evidence is ambiguous.** Missing or corrupt
   attempt records, unverifiable backup contents or restored data, and unresolved
   container or volume identity mismatches need evidence-based inspection.
   Preserve all evidence and escalate with the JSON status and relevant
   error/logs, even when the only reported code is `recovery_required`. Do not
   start A on data that has not been verified, and do not restore A over a durable
   accepted B. Phase 1 has no command that forces or completes an arbitrary
   restore by hand; any manual recovery needs a plan for that exact state.

### Status on the host and in the app

- `./bin/update status` prints the installed versions, an unfinished run and the
  last receipt. It works while Docker is down (running versions then show as
  unknown and it exits 2).
- `./bin/update status --format json` prints a host-only document
  (`host_schema_version` 1) with versions, the pending transaction, the last run
  and reason codes. It contains host paths: never serve it to the app.
- `./bin/update reporting init --directory /absolute/dedicated/public-parent`
  binds a dedicated host directory once. From then on every tick and control
  writes a sanitized status report into its slot: versions, discovery, the
  current operation and phase, the last run with its forward and restore
  results, the effective installed version, the quarantined release, the backup
  and loss-window times, the A6 estimates and a guidance ID (`none`,
  `inspect_host` or `review_recovery`). Setup does not do this for you; it is
  where the estimates and notify-only reasons are kept.
- The app's **Settings > Updates** page (Owner and Admin members only) is
  read-only. It shows the report only when the app container mounts the slot
  through the host-status overlay
  (`deploy/docker-compose.host-status.standalone.yml`; see
  `./bin/install --host-status-help`). That overlay's mount is not part of the
  qualified automatic layout, so on an install set up with `./bin/update setup`
  (any mode) the page says reporting is not connected and the host commands above
  are the way to see status. The page has no button that starts, approves or retries anything.

### Budgets

Setup records these bounds (defaults in [Scheduled runs](#scheduled-runs)):

- forward = backup + migration + target startup + stability + quiescence +
  metadata (6660 seconds with the defaults);
- recovery = restore + previous-version startup + stability + quiescence +
  metadata (5460 seconds);
- a scheduled run (`FUTURE_AGI_UPDATE_TICK_TIMEOUT_SECONDS`, 14400) must cover
  the image pull plus both.

Before stopping A, the updater checks again that the run has at least
max(forward + recovery, the A6 estimate) left; the forward attempt then runs
with the recovery budget held back. Larger budgets can be chosen at setup, for
example:

```bash
./bin/update setup --mode apply --wait-timeout-seconds 4200 \
  --recovery-startup-timeout-seconds 4200 --migration-timeout-seconds 4200 \
  --restore-timeout-seconds 3600 --tick-timeout-seconds 43200
```

These are examples of valid bounds, not measured product timings.

## Host automation reference

Run the source-checkout launcher, `./bin/update`, as the installation's original
host user. Python runs isolated; the application never invokes this CLI, reads
private automation state, runs a backup, or receives a Docker socket. Manual
mode and disabled automation remain the defaults. The legacy backup-hook
FUTURE_AGI_UPDATE_MODE still means installation type, not automation policy.

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
| `setup --mode MODE` | One-time opt-in (Standalone only), see [One-time automatic-update setup](#one-time-automatic-update-setup). Prints the summary, and for apply the disclosure, then asks unless `--yes`. Zero when active or `manual`; 3 when declined, incomplete, or an earlier disable/pause was kept; usage errors exit 2. |
| `setup --deactivate` | Disable first, then remove only the owned scheduler; asks unless `--yes`. Zero when removed; 3 with `automatic_recovery_pending` while an attempt is unresolved. Final for the registration. |

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
controls only: it neither installs nor starts a timer. These commands never change
git, merge env settings, install dependencies or restore databases.

### Restore qualification and daemon image capacity

**Complete qualification is still under native validation in this development
tree.** The release producer issues automatic eligibility only after the exact
source/target executor runs all six suites on real product images, validates the
raw measurements and confirms cleanup. Bounded M1–M5 milestones have passed on
native amd64 GitHub runners, but their outputs remain explicitly nonqualifying.
Component tests, fake-Docker results and stage-status JSON are not release
proofs; a missing measurement or failed assertion must never be waived to make
acceptance green. An unavailable executor reports `shipping_executor_unavailable`;
missing or failed suites and measurements remain explicit refusals.

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
supports two layouts. For a directly connected native Linux dockerd Unix-socket
peer in the same root/mount namespace using classic overlay2, it reads that image
store's free bytes. For Docker's containerd image store at its default paths
(`/var/lib/containerd` behind `/run/containerd/containerd.sock`), it measures
with a bounded, network-less, read-only helper container started from the
installed app image by image ID, never pulled. Remote Docker, SSH contexts, macOS
VM contexts and other drivers or paths have unknown capacity and refuse
absent-image pulls. It does not SSH to a VM, change a context or guess from
Docker system-df usage. Exact already-cached images can still be independently
verified.
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
