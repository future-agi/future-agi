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

Complete the release/backup review above. Use the verified local chart, the
intended context/namespace and the complete current values file:

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
