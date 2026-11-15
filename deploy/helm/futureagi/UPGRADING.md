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

## Legacy tag cutover barrier

**Owner decision required: schedule a maintenance outage for this transition.**
The supported procedure is a cold writer cutover, not two online passes. Budget
for the full scan, human review, retries, migrations and validation; no fixed
completion time is guaranteed. Pause unattended updates and all competing
operators/controllers first. Record the deployment owner, scope, backup/recovery
point and permission to keep the installation unavailable if checks fail.

1. Close admission to **all** application traffic, including direct backend
   ports, API clients, shared links, internal callers and ingestion, not just the
   frontend/ingress. Use the installation's external maintenance/access controls;
   this release has no application feature flag that supplies this barrier.
   Stop **all legacy and new editor/request writers**, plus workers, collectors,
   gateway and any external ingestion/mirror writers. Drain in-flight work and
   confirm it has stopped. Keep PostgreSQL and ClickHouse available to the
   operator. A frontend-only pause or a rolling backend replacement is not enough.
2. Keep the new UI and API inaccessible. With the verified new image, apply the
   reviewed schema prerequisites using one-off initialization jobs, not by
   starting a serving backend. Run a **full-scope final** reconciliation dry-run
   after the last legacy write. An earlier online rehearsal is not the final pass.
3. **STOP for human review** of scanned/differing/missing/changed counts and scope.
   Record approval, then run apply as a separate action. Never chain dry-run and
   apply with `&&`. Retain every segment's logs. Resume only the same pass/scope
   with its last `--after-id`; if **any** segment reports `changed during scan`,
   investigate the broken writer barrier and repeat the whole pass from the
   beginning, without `--after-id`. Successful exit alone is not completion.
4. While the barrier remains closed, run a fresh full dry-run (no `--after-id`)
   and require `would mirror 0` and `changed during scan 0`. Review missing CH
   rows separately: the command does **not** create them. Repair/reconcile them
   or explicitly exclude their projects from editor access with owner-approved
   recovery tracking; do not silently approve an incomplete scope. If your
   admission controls cannot isolate those projects, keep the entire editor
   unavailable. Verify known legacy tags in display and filtering using fresh
   reads before public exposure.
5. Only after reconciliation is complete and reviewed may new application
   processes start and the editor become available. Keep external admission
   closed during startup/validation. Discard pre-cutover grid/drawer selections,
   invalidate any response caches serving tag state, and require users to close
   old tabs and reload before editing. API clients must discard cached tag lists
   and refetch. Do not replay queued pre-maintenance tag requests. An old tab's
   first revision-zero save can hide a legacy edit even **after** reconciliation;
   an assignment/span revision is not a legacy-tag revision. If you cannot ensure
   fresh clients, keep editing unavailable. Reopen only after the owner signs off.

The unsupported sequence is pass1 `[A]`, acknowledged legacy write `[A,B]`, new
first save `[A,C]`, pass2 `[A,B]`: the assignment hides B permanently. A stale
save after pass2 has the same effect. Repeating reconciliation cannot repair an
already-created assignment; it never changes assignments. If the new editor was
exposed early, stop writes, preserve both stores and recover affected assignments
with the data owner before reopening. Do not treat another online pass as repair.

**Downgrade is not lossless.** Before accepting new edits, agree a recovery plan
that preserves/exports `tracer_entity_tag_assignment` from primary PostgreSQL
(including identity keys, manual tags, revisions and timestamps) with its related
project records and coordinated store backups. Protect that export as customer
data and test restoration. Image-only rollback leaves this table but the old
application ignores it; reverse migration drops it. There is no reverse
reconciliation/export command here. Span assignments and explicit clears cannot
be blindly converted into legacy trace tags. Choose preservation for re-upgrade,
an explicitly reviewed conversion, or coordinated restore with acknowledged loss
of writes after the recovery point; do not drop the table before preservation.

## Direct Helm

Before upgrading, inspect the running release without changing it:

```sh
helm status futureagi --kube-context CONTEXT -n futureagi
helm history futureagi --kube-context CONTEXT -n futureagi
kubectl --context CONTEXT -n futureagi get deployments,statefulsets,jobs,pvc
```

Complete the release/backup review and [legacy tag cutover barrier](#legacy-tag-cutover-barrier)
above before upgrading to the trace/span tag editor. The reconciliation is an
operator-run one-off Job, not a new hook: `bootstrap_install` does not do it.

For this chart, stop all application Deployments, including `futureagi-backend`,
`futureagi-frontend`, `futureagi-fi-collector`, `futureagi-agentcc-gateway`,
`futureagi-worker`, `futureagi-worker-exact-aggregation`,
`futureagi-worker-sql-workbench` and every configured dedicated `worker.queues`
Deployment (queue underscores become hyphens). Actual names depend on the
release/fullname override; discover them by the instance label rather than
assuming that list is exhaustive. Bundled datastores are StatefulSets and stay
up. Stop any external writers separately.

First suspend Argo/Flux/other reconcilers **in their source of truth**, stop any
active sync/upgrade, and disable competing autoscalers/reloaders. Save the current
replica counts and HPA manifests privately for recovery. With external admission
already closed, this example scales every Deployment of release `futureagi` in
namespace `futureagi` to zero (also pausing serving/code-executor):

```sh
kubectl --context CONTEXT -n futureagi get deployments,hpa -l app.kubernetes.io/instance=futureagi
kubectl --context CONTEXT -n futureagi get hpa -l app.kubernetes.io/instance=futureagi -o yaml > tag-cutover-hpa.yaml
kubectl --context CONTEXT -n futureagi delete hpa -l app.kubernetes.io/instance=futureagi
kubectl --context CONTEXT -n futureagi scale deployment -l app.kubernetes.io/instance=futureagi --replicas=0
kubectl --context CONTEXT -n futureagi get deployments,pods -l app.kubernetes.io/instance=futureagi
```

**STOP and verify** every application Deployment has zero replicas and there are
no running or terminating application pods/in-flight writers. Inspect pending
Jobs too; only the reviewed operator initialization/reconciliation may write.
Do not proceed merely because `scale` returned success. `backend.replicas: 0`
alone is insufficient: HPAs can restore replicas; per-queue `replicas: 0` is
rendered as 1, and SQL-workbench/exact-aggregation workers have fixed replicas.
There is no chart-wide maintenance value. Keep controllers paused and admission
closed until the final reconciliation is reviewed. Apply reviewed schema
prerequisites as isolated bootstrap Jobs under this barrier before reconciliation;
do not run an upgrade that restarts Deployments just to get the schema ready.

Use the **verified new backend image**, current values, namespace and bootstrap
Job's existing datastore credentials, mounts and operator environment
(`SERVICE_TYPE=bootstrap` and `STARTUP_DB_MUTATION_MODE=operator`); an ordinary
backend pod's `NO_STARTUP_DB_MUTATIONS=true` does not authorize this command.

Render only the bootstrap Job from the verified chart. The following one-off
Job reuses its backend image, service account, env and volume configuration,
changes its name and command, removes hook annotations and the bootstrap's
18-minute `activeDeadlineSeconds` and its `ttlSecondsAfterFinished` (so the
Job and its logs stay until this function deletes it), and disables automatic
retries by setting `backoffLimit` to `0`. A large scan may run longer than the bootstrap Job;
watch its progress logs from another shell if needed.
Python 3 with PyYAML is already a prerequisite for preparing the signed release.
Complete release-specific schema prerequisites under the writer barrier before
running this Job. Do not publish the rendered Job if its env or values contain
credentials. Keep the barrier closed throughout the following separate actions.

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
reconcile_tags --dry-run
```

**STOP for human review** of the counts and scope. Retain the logs and record
approval before running the mutating action, in the same operator shell (or
redefine the function above). No serving pod may start yet.

```sh
reconcile_tags
```

Inspect every segment's `changed during scan` count. Any nonzero count requires
investigating the writer barrier and a whole repeat without `--after-id`. Job
success alone does not authorize reopening. Once apply is complete, verify from
the beginning and review `would mirror 0`, `changed during scan 0` and missing rows:

```sh
reconcile_tags --dry-run
```

On a failure, read the printed logs: each progress line includes a final
`resume with --after-id <cursor>`. Diagnose and repair the failure, retain the
logs, then **delete the failed Job** with
`kubectl --context CONTEXT -n futureagi delete job futureagi-reconcile-legacy-tags --wait=true`.
Rerun the *same* pass with its original flags plus the cursor. A failed dry-run
resumes with `reconcile_tags --dry-run --after-id <cursor>`; review all segments
before separately starting apply from the beginning. A failed apply resumes with
`reconcile_tags --after-id <cursor>`. With no cursor, restart that pass without
`--after-id`. On a timeout, the Job may still be running: inspect or stop it
before deleting it; never start another Job over a running scan. Do not proceed
to rollout until final reconciliation and verification are complete and reviewed. A failed Job cannot be retried
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

Keep external admission closed during the rollout. Verify fresh tag display and
filtering, enforce the cache/tab reload requirements above, and obtain owner
sign-off before reopening. Restore the intended replica/HPA configuration and
controller ownership from the reviewed values/source of truth; do not blindly
reapply an old HPA over changed release values.

The new editor stores manual assignments, not PG `tracer_trace.tags`.
Langfuse ingestion still updates that PG column and mirrors traces to CH;
this one-time handoff does not replace ongoing ingestion mirroring or protect
against later collector root-span resets. For Argo CD/Flux, use the **same cold
writer barrier** and final reconciliation before syncing the reviewed release.
Do not let automated promotion/self-heal restart pods during the scan. The
pre-sync/bootstrap hook alone is not a writer barrier.

## Standalone

Standalone's `app` service has no automatic reconciliation step in
`deploy/standalone/bin/bootstrap.py`. Follow the [barrier and recovery requirements](#legacy-tag-cutover-barrier)
above. This requires downtime: `app` contains the API, frontend, collector,
gateway and Temporal workers; stopping only its web process is insufficient.
Stop any separately deployed writers too. Pause automatic updates and complete
the [manual handoff](../../update/README.md#safe-handoff-to-manual-operations)
before changing services. From the repository root, back up both stores and
pin/pull the verified new image without replacing the old `app` first (or stage
with `./bin/update apply --stage`). `--no-deps` below prevents accidental startup;
`--entrypoint python` bypasses supervisord. The operator pair is required even
for dry-run.

```sh
docker compose pull app
# Close external admission, drain requests, then stop ALL app writers.
docker compose stop app
docker compose ps --status running
# Verify app is stopped; postgres/clickhouse must remain available.
# Apply reviewed schema prerequisites using one-off commands, never by starting app.
docker compose run --rm --no-deps -e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator \
  --entrypoint python app manage.py migrate --noinput
docker compose run --rm --no-deps -e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator \
  --entrypoint python app manage.py reconcile_legacy_trace_tags --dry-run
```

**STOP for human review** of counts/scope; record approval before applying.

```sh
docker compose run --rm --no-deps -e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator \
  --entrypoint python app manage.py reconcile_legacy_trace_tags
```

Retain logs; handle resume/changed/missing rows as required above. Then verify a
full scan from the beginning while `app` remains stopped:

```sh
docker compose run --rm --no-deps -e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator \
  --entrypoint python app manage.py reconcile_legacy_trace_tags --dry-run
```

Only after final reconciliation is complete and reviewed may you start the new
app. For a staged signed update, complete with `./bin/update apply` instead of
manually running `up`:

```sh
docker compose up -d --wait
```

Keep admission closed during startup, verify fresh display/filtering, discard
stale tabs/caches and requests, then obtain sign-off before reopening. The
Standalone Django config uses the `clickhouse` service address for the one-off
container, not the in-app collector's loopback address. `migrate` above covers
Django only; any additional release-specific CH/native prerequisites must also
be completed under the barrier before reconciliation. Never start the editor
just to initialize those schemas. Automatic updates do not perform this handoff.

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
