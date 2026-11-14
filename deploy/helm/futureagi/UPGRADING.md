# Verified Kubernetes upgrades

This is the operator-controlled Helm, Argo CD and Flux update path. It reuses
Future AGI's published chart, image digests and bootstrap job. It does not install
an in-cluster updater or give the application cluster-administration credentials.
The Compose automatic updater and its compatibility proofs do not authorize a
Kubernetes transition: storage, controllers and recovery procedures differ.

## Before approving a release

1. Read the target release notes, migration requirements and chart value changes.
   Do not assume that a patch/minor number proves schema compatibility. Review
   the running old version's ability to use the schema during migration and after
   a failure. If that cannot be established, use an operator-controlled maintenance
   outage rather than a rolling deployment with old writers still running.
2. Identify a recoverable, coordinated backup of PostgreSQL, ClickHouse, object
   storage, Temporal persistence and the application's encryption keys and secrets.
   Have a tested restore procedure for the actual external services/PVCs you use.
   A successful snapshot request or a previous Helm revision alone is not proof
   that the application data can be recovered. Follow the chart's
   [backup guidance](README.md#backup-and-restore).
3. Keep one deployment owner: direct Helm **or** Argo CD **or** Flux. Do not run
   `helm upgrade` against a release while another controller manages it. Commit
   non-secret values and keep credentials in existing Kubernetes Secrets or your
   secret-management system. Preserve installed datastore/PVC identities.
4. Authenticate the exact chart before allowing it into the deployment repository.
   Pin the chart digest and the target release's signing identity together. Retain
   the verification record beside the reviewed change, without credentials.

## Prepare a signed release without changing the cluster

From a trusted checkout of this repository, use Python 3.9+ with PyYAML, Helm 4,
ORAS and cosign available on the operator host. The release must already have
been published and signed; an unreleased source checkout is not a substitute.
Use the helper shipped with the source revision you reviewed:

```sh
python3 deploy/helm/futureagi/hack/prepare-release.py \
  --version X.Y.Z --destination "$HOME/futureagi-release-X.Y.Z"
```

The destination must not exist. The helper:

- Resolves the exact chart version to an OCI digest, then verifies that digest's
  classic cosign signature against `helm-release.yml` at **that exact `vX.Y.Z`
  tag**, with the GitHub Actions OIDC issuer. A provenance attestation alone is
  not accepted instead of the chart signature.
- Pulls the chart by the verified digest, not by a tag that could move between
  verification and download. Refuses unexpected chart names/versions,
  dependencies, missing component digest pins, component defaults that override
  those pins, and unsafe archive members.
- Writes the chart `.tgz` and a private `release-lock.json` containing its hash,
  publisher identity, component digests and the exact Argo CD/Flux source fields.
  It performs no cluster action, Git commit or release publication.

**This establishes publisher identity and artifact integrity, not migration
compatibility, available backup capacity or permission to restore customer data.**
The JSON explicitly records `migration_compatibility_qualified: false`. Do not
promote it into a Phase 1 compatibility proof or treat it as an unattended-apply
approval. Re-run verification if the downloaded chart or its evidence changes.

Apply only reviewed overrides. Setting `image.tag`, a component tag/repository or
`image.pinDigests: false` can bypass the published image selection. Do not use
those overrides in this verified-release path. A registry mirror must preserve
the authenticated image digests; arbitrary replacement images need their own
review and qualification.

## Direct Helm

Before upgrading, inspect the running release without changing it:

```sh
helm status futureagi --kube-context CONTEXT -n futureagi
helm history futureagi --kube-context CONTEXT -n futureagi
kubectl --context CONTEXT -n futureagi get deployments,statefulsets,jobs,pvc
```

Complete the release/backup review above. For upgrades introducing the
trace/span tag editor, reconcile legacy PG tags before **and** after the
rollout. This is a documented one-off Job, not a new chart hook: the chart's
pre-upgrade bootstrap Job only runs `bootstrap_install`, and it completes
*before* the old backend stops accepting PG-only tag PATCH requests. A hook
would miss edits made after its scan. Use the **verified new backend image**,
current values, namespace and bootstrap Job's existing datastore credentials,
mounts and operator environment (`SERVICE_TYPE=bootstrap` and
`STARTUP_DB_MUTATION_MODE=operator`); an ordinary backend pod's
`NO_STARTUP_DB_MUTATIONS=true` does not authorize this command.

Render only the bootstrap Job from the verified chart. The following one-off
Job reuses its backend image, service account, env and volume configuration,
changes its name and command, removes hook annotations and the bootstrap's
18-minute `activeDeadlineSeconds` and its `ttlSecondsAfterFinished` (so the
Job and its logs stay until this function deletes it), and disables automatic
retries by setting `backoffLimit` to `0`. A large scan may run longer than the bootstrap Job;
watch its progress logs from another shell if needed.
Python 3 with PyYAML is already a prerequisite for preparing the signed release.
Check any release-specific schema prerequisites before running it against the
old schema. Leave the old backend serving until the first apply finishes. There
is no editor gate during the rollout: run the second apply immediately after
rollout to minimize the window in which new-editor edits can occur. Do not
publish the rendered Job if its env or values contain credentials.

Run in bash (no `set -e`): the function prints logs even when the Job fails,
then preserves that failed Job for inspection. The two condition waits poll
until either Complete or Failed, for at most `RECONCILE_WAIT_TIMEOUT_SECONDS`
(default 7200 seconds). Set a larger positive number in the operator shell for
large scans; the Job itself has no scan deadline. On a wait timeout the function
prints the logs and last cursor and returns nonzero, but the Job may still be
running: inspect or stop it before retrying. If you interrupt the shell, inspect
the Job and its logs before retrying.

```bash
set -o pipefail
NEW=X.Y.Z
reconcile_tags() {
  local wait_seconds="${RECONCILE_WAIT_TIMEOUT_SECONDS:-7200}"
  if [[ ! "$wait_seconds" =~ ^[1-9][0-9]*$ ]] ||
     (( ${#wait_seconds} > 5 )) || (( wait_seconds > 86400 )); then
    echo 'RECONCILE_WAIT_TIMEOUT_SECONDS must be 1..86400 seconds' >&2
    return 1
  fi
  helm template futureagi "$HOME/futureagi-release-$NEW/futureagi-$NEW.tgz" \
    -n futureagi -f my-values.yaml --show-only templates/bootstrap/job.yaml |
    python3 -c '
import sys, yaml
job = yaml.safe_load(sys.stdin)
job["metadata"]["name"] = "futureagi-reconcile-legacy-tags"
job["metadata"].pop("annotations", None)
job["spec"].pop("activeDeadlineSeconds", None)
job["spec"]["backoffLimit"] = 0
job["spec"].pop("ttlSecondsAfterFinished", None)
container = job["spec"]["template"]["spec"]["containers"][0]
container["command"] = ["python", "manage.py", "reconcile_legacy_trace_tags"]
container["args"] = sys.argv[1:]
yaml.safe_dump(job, sys.stdout)
' "$@" | kubectl --context CONTEXT -n futureagi create -f - || return 1
  local complete=false timed_out=false logs last_cursor logs_status=0
  local deadline=$((SECONDS + wait_seconds))
  while :; do
    if (( SECONDS >= deadline )); then
      timed_out=true
      break
    fi
    if kubectl --context CONTEXT -n futureagi wait \
      --for=condition=complete job/futureagi-reconcile-legacy-tags --timeout=10s >/dev/null 2>&1; then
      complete=true
      break
    fi
    if kubectl --context CONTEXT -n futureagi wait \
      --for=condition=failed job/futureagi-reconcile-legacy-tags --timeout=1s >/dev/null 2>&1; then
      break
    fi
  done
  logs=$(kubectl --context CONTEXT -n futureagi logs job/futureagi-reconcile-legacy-tags 2>&1) || logs_status=$?
  printf '%s\n' "$logs"
  last_cursor=$(printf '%s\n' "$logs" | sed -nE 's/.*resume with --after-id ([[:xdigit:]-]{36}).*/\1/p' | tail -n 1)
  if [ "$timed_out" = true ]; then
    echo "Reconcile Job wait timed out; last cursor: ${last_cursor:-none}. Job may still be running; inspect or stop it before retrying." >&2
    return 1
  fi
  if (( logs_status != 0 )); then
    echo "Could not read Reconcile Job logs; inspect the Job before retrying." >&2
    return 1
  fi
  if [ "$complete" != true ]; then
    echo "Reconcile Job failed; last cursor: ${last_cursor:-none}. Keep it for inspection, then delete and resume the same pass." >&2
    return 1
  fi
  kubectl --context CONTEXT -n futureagi delete job futureagi-reconcile-legacy-tags --wait=true
}
# Review the dry-run counts; the first apply runs only if the dry-run succeeds.
reconcile_tags --dry-run && reconcile_tags  # first apply while the old backend serves
```

Inspect the final `changed during scan` count in each Job's logs (including the
second apply below). After a resume with `--after-id`, check each run's own
`changed during scan` count: if any run of that pass reported a nonzero count,
repeat the whole pass from the beginning, without `--after-id`, before moving
to the next step. The Job completes successfully despite concurrent edits so
it does not block the scripted two-pass rollout; operators must act on the
logged count.

On a failure, read the printed logs: each progress line includes a final
`resume with --after-id <cursor>`. Diagnose and repair the failure, retain the
logs, then **delete the failed Job** with
`kubectl --context CONTEXT -n futureagi delete job futureagi-reconcile-legacy-tags --wait=true`.
Rerun the *same* pass with its original flags plus the cursor. A failed dry-run
resumes with `reconcile_tags --dry-run --after-id <cursor> && reconcile_tags`:
the first apply still scans from the beginning. A failed apply resumes with
`reconcile_tags --after-id <cursor>`. With no cursor, restart that pass without
`--after-id`. On a timeout, the Job may still be running: inspect or stop it
before deleting it; never start another Job over a running scan. Do not proceed
to the rollout until the first apply succeeds. A failed Job cannot be retried
by applying or creating another Job with the same name.
Use the verified local chart, intended context/namespace and complete current
values file:

```sh
helm upgrade futureagi "$HOME/futureagi-release-X.Y.Z/futureagi-X.Y.Z.tgz" \
  --kube-context CONTEXT -n futureagi -f my-values.yaml --wait --timeout 20m
helm test futureagi --kube-context CONTEXT -n futureagi --logs --timeout 5m
```

Do not pass `--reuse-values` across chart versions. Do not enable automatic
image-only rollback (`--atomic` in Helm 3 or `--rollback-on-failure` in Helm 4)
unless that exact transition is known to tolerate it. A failed migration can
leave changed data even when no new Deployment was rolled out.

The bootstrap Job has its own deadline. A Helm timeout is not evidence that the
Job stopped: inspect its terminal state before another upgrade, rollback or
restore. Keep failed Job diagnostics; do not repeatedly replace a running Job.

For the trace/span tag-editor upgrade, run the second apply immediately after
the new backend rollout. The new editor is already available during this window;
there is no built-in gate to hold it back. Run this in the **same operator shell**
as the first pass (or redefine `reconcile_tags` from the first block before
proceeding):

```sh
kubectl --context CONTEXT -n futureagi rollout status deployment/futureagi-backend --timeout=20m
reconcile_tags            # second apply after rollout; catch old-backend edits
# If it reports rows changed during the scan, rerun the apply.
```

The new editor stores manual assignments, not PG `tracer_trace.tags`.
Langfuse ingestion still updates that PG column and mirrors traces to CH;
this one-time handoff does not replace ongoing ingestion mirroring or protect
against later collector root-span resets. For Argo CD/Flux, run the same dry-run
and apply before syncing the reviewed release, and run the second apply immediately
after the new backend is healthy. Automated promotion can expose the editor
between passes; minimize that window rather than assuming it can be gated.

## Standalone

Standalone's `app` service also has no reconciliation step in its
`deploy/standalone/bin/bootstrap.py`. This is an operator-run one-time upgrade
handoff, not a step to run on every boot. With a cold stop of the old app, one
apply after its last write is sufficient; when the old app keeps serving during
image staging, use the two applies below to catch edits between the first scan
and the swap. From the repository root, back up both stores and pin/pull the
new Standalone image without replacing the old `app` container first. Use
`docker compose run` with the new image and existing PostgreSQL/ClickHouse
services: `--no-deps` leaves the old app serving, and
`--entrypoint python` bypasses the standalone supervisord startup. The
operator authorization pair is needed even for the dry-run because the startup
guard treats this management command as mutating.

```sh
docker compose pull app
# Review any release-specific schema prerequisites before reconciling.
docker compose run --rm --no-deps -e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator \
  --entrypoint python app manage.py reconcile_legacy_trace_tags --dry-run
# Review the reported count before applying.
docker compose run --rm --no-deps -e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator \
  --entrypoint python app manage.py reconcile_legacy_trace_tags
docker compose up -d --wait
# Old-app PATCH writes between the first pass and the swap need this second pass;
# run the second apply immediately after rollout. The editor may already be live.
docker compose run --rm --no-deps -e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator \
  --entrypoint python app manage.py reconcile_legacy_trace_tags
# Rerun the apply if it reports rows changed during the scan.
```

The Standalone image's Django config uses the `clickhouse` service address for
this one-off container, not the in-app collector's loopback address. If the
release needs migrations before the first pass, follow its reviewed maintenance
plan; never bring up a new backend on an old schema just to run the command.
Automatic updates have a separate lifecycle; pause them and follow the safe
handoff before manual changes.

## Argo CD

Use an Argo CD version with native OCI application-source support. The example
uses `repoURL: oci://ghcr.io/future-agi/charts/futureagi`, `path: .` and a digest in
`targetRevision`, not the older tag-only Helm repository source shape.

1. The platform operator creates the destination namespace and applies
   [argocd-project.yaml](examples/gitops/argocd-project.yaml). The AppProject
   allows only this chart repository and that namespace, with no cluster-scoped
   resources or application RBAC creation. This is an additional project boundary,
   not a claim that it replaces the controller's Kubernetes RBAC configuration.
2. Pre-create the application's seven keys and datastore credentials as described
   in [Secrets](README.md#secrets). Argo's `helm template` cannot use Helm `lookup`
   to preserve generated keys; never rely on random values during reconciliation.
3. Copy [argocd-application.yaml](examples/gitops/argocd-application.yaml), set the
   environment's non-secret values and use the prepared `argocd_source` fields.
   A protected Git review is the signature-enforcement boundary: **Argo CD does
   not natively enforce this example's cosign identity policy.** Restrict who can
   change the digest and require verification before promotion.
4. Approve and sync the complete Application. The example permits automatic sync
   of reviewed changes, but disables automatic pruning, self-heal and retry loops.
   Do not enable selective sync or `ApplyOutOfSyncOnly`: those can skip hooks.

The chart's bootstrap is PreSync when every datastore is external. With bundled
stores it is a Sync hook in wave -1, after the stores in wave -2 and before the
application in wave 0. Existing old pods are not automatically quiesced by those
waves. Use the migration review/maintenance plan above for active writers.

Inspect the Application's desired revision, observed sync revision, health and
operation state, then the actual Job and workload rollouts:

```sh
kubectl --context CONTEXT -n argocd get application futureagi -o yaml
kubectl --context CONTEXT -n futureagi get jobs,deployments,statefulsets,pvc
kubectl --context CONTEXT -n futureagi logs job/futureagi-bootstrap
```

Treat the Application YAML as operator-only output: inline values may contain
secrets if your deployment did not follow the existing-Secret guidance. Do not
publish it as an application status endpoint or paste it into a public issue.
A green sync for a different digest, or a successful old hook, is not the target
release's health evidence. Check the observed revision and current operation.

## Flux

**Existing installations:** these examples describe a new, namespace-scoped
installation, not a namespace or ownership migration. Preserve your existing
HelmRelease name/namespace, Helm release name, target namespace and storage
namespace unless you have a separately reviewed migration plan. Do not delete
and recreate the old HelmRelease or apply a second one to adopt this example:
that can uninstall the old release or leave two owners reconciling it. Moving
from a control namespace such as `flux-system` also requires an explicit review
of Secret storage and ServiceAccount/RoleBinding scope; it is not a chart upgrade.

Use [flux-rbac.yaml](examples/gitops/flux-rbac.yaml) as an operator-applied,
namespace-scoped boundary and [flux-helmrelease.yaml](examples/gitops/flux-helmrelease.yaml)
for reconciliation. The controller impersonates `futureagi-reconciler`; application
and bootstrap pods continue to use their own tokenless ServiceAccounts. No
ClusterRole, RoleBinding creation permission, pod exec or namespace mutation is
granted to this reconciler. The platform's Flux installation remains separate.

Replace `OCIRepository.spec.ref.digest` and its exact OIDC subject together using
`release-lock.json`'s `flux_source`. Put your non-secret values in
`futureagi-values` in the same namespace, with credentials supplied through
existing Secrets. Never add a semver range or a second mutable image updater.

Flux verifies the chart's cosign signature before releasing its source artifact.
The example explicitly selects `RemediateOnFailure`, with zero retries and no
last-failure remediation for both install and upgrade. Thus a failed attempt
stops visibly, rather than repeatedly running migrations, uninstalling data-bearing
resources or automatically choosing an image rollback. Helm tests are enabled;
failures are not ignored.

```sh
kubectl --context CONTEXT -n futureagi get ocirepository futureagi -o yaml
kubectl --context CONTEXT -n futureagi get helmrelease futureagi -o yaml
kubectl --context CONTEXT -n futureagi get jobs,deployments,statefulsets,pvc
```

Check `SourceVerified` and `Ready` for the current source generation, the actual
source artifact digest, and the HelmRelease's current observed generation,
attempted digest and test result. A stale Ready condition is insufficient. The
source must match the reviewed digest; application readiness must be checked too.

## Failed rollout and recovery

1. Stop further deployment changes. Disable Argo automated sync or suspend the
   Flux HelmRelease **in its source of truth**, so an outer ApplicationSet or
   GitOps reconciliation cannot immediately undo the pause. Suspending does not
   cancel an operation already in progress. Confirm the active operation and
   bootstrap Job have finished before manual mutation.
2. Preserve controller events, the failed Job's logs, the intended chart digest,
   values revision, installed image identities and backup identifiers. Keep
   credentials and raw Helm/Secret output out of public diagnostics.
3. Choose recovery for the observed state. A failed image rollout with no
   incompatible data change may permit an image/configuration rollback. A partial
   or incompatible migration requires the deployment-specific data restore plan;
   a newer chart becoming Healthy does not itself prove data preservation.
4. If restoration is required, stop application writers and reconcile PostgreSQL,
   ClickHouse, object storage, Temporal state and encryption keys to a consistent
   recovery point. Do not have old and recovered instances accept divergent writes.
   Follow the tested procedure for your datastore operators or backup system.
5. Make the intended recovered release the Git source of truth before resuming
   reconciliation. Check login, an existing trace/evaluation and an existing file,
   not just pod readiness. Resume only after that evidence is satisfactory.

Argo CD rollback does not run Helm's pre-rollback hooks, and automated sync must
be disabled before an Argo rollback. With Flux, suspend the HelmRelease before
manual Helm recovery and reconcile Git before resuming. Do not operate one
controller's release using the other controller.

**There is no universal Kubernetes database-restore command or zero-loss
promise here.** Snapshot authority, operators, external services and retained
state determine the recovery path. Image/chart signature verification and a
Helm revision rollback cannot substitute for those facts.
