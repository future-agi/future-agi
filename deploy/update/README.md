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
fails before anything restarts leaves the file exactly as it found it.

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
if it had been recovered.

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
