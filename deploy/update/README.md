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
| 3 | Refused before anything changed: the plan blocks the update (including a signature that could not be verified, e.g. no usable cosign), no backup is configured, a production update is not staged, a readiness setting is invalid, or `rollback` needs `--yes` | Do what the message says; `check` lists every plan reason |
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
set `FUTURE_AGI_UPDATE_ENABLED=true`. With the defaults (`manual` and
disabled) a scheduled run does nothing. Do not confuse
`FUTURE_AGI_UPDATE_AUTOMATION_MODE` with `FUTURE_AGI_UPDATE_MODE`: the backup
hook receives the second one as the install type (Standalone, Distributed or
production).

| Key | Default | What it does |
| --- | ------- | ------------ |
| `FUTURE_AGI_UPDATE_AUTOMATION_MODE` | `manual` | How far a scheduled run goes: `manual` (nothing), `notify` (check and report), `download` (also fetch and verify the images) or `apply` (also update, inside the maintenance window). |
| `FUTURE_AGI_UPDATE_ENABLED` | `false` | Master switch. While `false`, a scheduled run does nothing in any mode. |
| `FUTURE_AGI_UPDATE_INTERVAL_SECONDS` | `21600` | Time between scheduled release checks: 300 to 86400. |
| `FUTURE_AGI_UPDATE_MAX_BACKOFF_SECONDS` | `86400` | Longest wait after repeated failures: 300 to 86400, and not less than the interval. |
| `FUTURE_AGI_UPDATE_TIMEZONE` | `UTC` | IANA time zone name of the maintenance window (for example Europe/Berlin, written exactly as in the tz database). It must exist in the host's tzdata. |
| `FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW` | none | When a scheduled apply may start. Empty means never. Otherwise `always`, or `HH:MM-HH:MM` in the time zone above (two different times; it may cross midnight). |
| `FUTURE_AGI_UPDATE_MAINTENANCE_DAYS` | `MON,TUE,WED,THU,FRI,SAT,SUN` | Days a window may start on, from `MON` to `SUN`, comma-separated, each at most once. |
| `FUTURE_AGI_UPDATE_MIN_START_BUDGET_SECONDS` | `900` | A scheduled apply starts only if at least this much of the window is left: 60 to 7200. |
| `FUTURE_AGI_UPDATE_BACKUP_MAX_AGE_SECONDS` | `3600` | Oldest backup a scheduled apply accepts: 60 to 86400. |
| `FUTURE_AGI_UPDATE_STAGE_MAX_AGE_SECONDS` | `86400` | How old a staged update may be before a scheduled apply refuses to finish it: 300 to 604800. |
| `FUTURE_AGI_UPDATE_AUTOMATIC_ROLLBACK` | `false` | Allow one automatic images-only fallback after a failed scheduled apply, and only for a release whose signed manifest certifies it. It never restores a database or reverses a migration. |
| `FUTURE_AGI_UPDATE_PROBE_TIMEOUT_SECONDS` | `60` | Longest a single Docker or Compose status probe may take: 5 to 300. |
| `FUTURE_AGI_UPDATE_PULL_TIMEOUT_SECONDS` | `1800` | Longest an image pull may take: 60 to 7200. |
| `FUTURE_AGI_UPDATE_TICK_TIMEOUT_SECONDS` | `14400` | Longest a whole scheduled run may take: 900 to 172800. In `apply` mode it must cover the backup timeout, one pull, twice the wait timeout and 600 seconds; with automatic rollback, the backup timeout, two pulls, four times the wait timeout and 1200 seconds. |

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
