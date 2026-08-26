# CATALOG DEV SELECT-only qualification materializer

`materialize.py` turns a reviewed current-source qualifier bundle into one
DEV-only Kubernetes Job plus its prerequisites. It is intentionally offline:
it reads files passed on the command line and never invokes `kubectl`, `gcloud`,
a container runtime, or a network client.

It fails closed unless all of these are exact:

- a digest-pinned derived image and verified bundle artifact inventory;
- a GKE context bound to the configured DEV GCP project, with that exact DEV
  namespace set on the context, and a matching active local gcloud project;
- a dedicated no-token qualifier ServiceAccount and separate purpose-built
  runtime-read and image-pull Secret names;
- an isolated `fi_catalog_dev_*` database and sorted workspace UUID
  allowlist, with unified catalog reads enabled and every catalog write/control
  setting disabled or blanked by explicit container environment values;
- default-deny ingress/egress plus DNS and exact `/32` TCP database egress;
- one shard, one completion, no retry, and exact green predecessor results in
  `whatfix`, `colektia`, `mudflap`, `trace_system`, `whatfix_graphs`,
  `colektia_graphs` order.

The materializer never emits a Secret. The runtime Secret must be created and
reviewed separately. Its sanitized contract digest belongs in
`read_only_secret_contract_sha256`; that review must establish a PostgreSQL
principal whose transactions are server/default read-only, distinct
server-locked read-only identities for source and catalog ClickHouse, no broker
or writer credentials, and no SOS/API token. The Job additionally applies
`PGOPTIONS=default_transaction_read_only`, SQL lexical guards, ClickHouse
SELECT/WITH guards, and mutation-dispatch tripwires. These application guards
do not replace server-side grants.

## Explicitly forbidden

Do not use SOS to obtain credentials. `SOSService.issue_sos_tokens()` creates
two `AuthToken` database rows and writes token data to cache, so it is outside
this SELECT-only boundary. The qualifier authenticates an existing principal
directly in-process and requires `QUALIFIER_SOS_FORBIDDEN=true`.

Do not use `deploy/dev/property-catalog` or the property-catalog rollout CLI as
part of qualification. Those are mutation-capable bootstrap/control-plane
paths. Do not point this package at production, and do not substitute a general
backend Secret.

## Required private inputs

Start with `config.example.yaml`, but store the completed copy outside the
repository. It needs the reviewed DEV project/context/namespace, dedicated
resource names, derived image digest, sanitized runtime-Secret contract digest,
isolated catalog database, exact Kartik workspace UUID(s), and database endpoint
IP/port allowlist. Hostnames and CIDR ranges are rejected; every database target
must be a single safe IPv4 `/32`.

The other inputs are:

- a freshly assembled bundle outside the repository;
- the kubeconfig file whose current context is the reviewed DEV context;
- the gcloud `active_config` file and its configurations directory;
- for every shard after the first, the exact JSON stdout result from each prior
  green shard in order.

The local configurations are evidence only. Before any authorized launch, an
operator must separately verify live cluster identity, that the namespace and
ServiceAccount have no RBAC/workload-identity grant, that no additive
NetworkPolicy or cluster-wide policy broadens egress, that both database
identities are server locked read-only, and that the referenced Secrets exist
with the reviewed contract. None of those live checks is performed here.

## Offline materialization

First assemble the source-bound bundle as documented in
`futureagi/scripts/fi_current_select_qualifier/README.md`, build the derived
image from that bundle, push it, and record the registry-reported digest. Image
build/push is an operator action and is not performed by either Python tool.

Validate without writing output:

```sh
python3 deploy/dev/fi-select-qualifier/materialize.py \
  --bundle /private/reviewed-fi-bundle \
  --config /private/fi-dev-qualifier.yaml \
  --kubeconfig /private/dev-kubeconfig \
  --gcloud-active-config /private/gcloud/active_config \
  --gcloud-configurations-dir /private/gcloud/configurations \
  --shard whatfix \
  --run-id fi-dev-qualification-001 \
  --frozen-end 2026-08-15T12:00:00Z \
  --check
```

Use a current frozen end (not future-dated and at most ten hours old). Once the
inputs have been reviewed, replace `--check` with a fresh output directory:

```sh
python3 deploy/dev/fi-select-qualifier/materialize.py \
  ...same reviewed arguments... \
  --output-directory /private/fi-whatfix-manifests
```

The directory contains `00-prerequisites.yaml` (ServiceAccount and both
NetworkPolicies) and `10-job.yaml` (only the Job). They are separate on purpose:
an authorized operator must apply and verify the prerequisites before applying
the Job, always with the exact explicit context and namespace. Never apply the
directory recursively or combine the two files in one apply operation.

Wait for the fixed-name Job to terminate, preserve its single JSON stdout line,
and confirm it is green before removing the completed Job and materializing the
next shard. Pass predecessor results in order, for example:

```sh
python3 deploy/dev/fi-select-qualifier/materialize.py \
  ...same source, target, run-id, and frozen-end... \
  --shard mudflap \
  --prior-result /private/result-whatfix.json \
  --prior-result /private/result-colektia.json \
  --output-directory /private/fi-mudflap-manifests
```

The fixed Job name prevents a second shard from coexisting through this
workflow. Kubernetes still treats NetworkPolicy allowances as additive, which
is why the live namespace-policy audit above remains a hard prerequisite.

## Offline tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s deploy/dev/fi-select-qualifier -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  deploy/dev/fi-select-qualifier/*.py \
  futureagi/scripts/fi_current_select_qualifier/*.py
```

The tests use temporary files only. They make no network, database, cloud,
container, Kubernetes, or deployment calls.

## 0816h two-phase DEV analogue

`kartik_smoke_0816h.py` and `kartik_smoke_0816h_run_contract.json` preserve the
bound, independently audited wrapper and run contract for the isolated 0816h
retry. The final 0816j image, activation, assembly, qualifier, wrapper, runner,
and environment pins are fixed; no assembly placeholder remains. The recorded
contract state is:

- `binding_state.state: BOUND_AUDITED_DEV_GO`;
- `binding_state.placeholder_count_remaining: 0`;
- both post-binding audit and human DEV approval booleans set to `true`;
- `execution_authorized: true` and `execution.approval_required: false`.

Copy the bound wrapper, runner, and contract to their contract-declared
mode-0600 bundle paths (the runner path is `phase-runner-0816h.py`). Run both
phases only through the same long-lived host process:

```sh
python3 /home/ubuntu/fi-dev-qualifier-current-0816h/bundle/phase-runner-0816h.py \
  --contract \
  /home/ubuntu/fi-dev-qualifier-current-0816h/bundle/kartik-smoke-0816h-run-contract.json
```

`phase_runner.py` requires the exact unchanged 68-key env-file inode, device,
size, owner, mode, and confidential content for the entire run. It invokes the
reviewed argv lists with `shell=False`, creates every capture with `O_EXCL` and
mode `0600`, and will not reach the matrix command until the registry JSON and
sealed handoff are green and cross-bound. Acceptance additionally requires the
exact 108 unique voice/trace/span window/profile identities, all callback
routes under 9.8 seconds, identical activation/source/tenant/target bindings,
and destruction of the sealed handoff after matrix loading. It never emits the
confidential env content digest.

The focused offline gate is:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 \
  deploy/dev/fi-select-qualifier/test_phase_runner.py
```

## SSH-host target

`materialize_ssh.py` is the separate path for a DEV machine reached through a
saved OpenSSH alias. It makes no Kubernetes assumption. It parses the supplied
SSH config and `known_hosts` files without invoking `ssh`, requires one exact
hostname/user/port/dedicated identity file with `IdentitiesOnly yes` and an
Ed25519 host-key digest, then
emits a private JSON plan containing a hardened Docker/Podman argument vector.
The argument vector is data, not a shell command, and the materializer never
executes it. The plan joins only the reviewed purpose-built
`fi-qualifier-dev-readonly` container network; it does not assume the
existing backend container or its startup path is safe to reuse.

This target also requires two sanitized, hash-bound JSON attestations:

- the read-only env contract lists only the minimum PG, source ClickHouse,
  catalog ClickHouse, and Django secret keys and asserts distinct server-locked
  `readonly=2` identities, PG server-default read-only, and absence of
  SOS/broker tokens;
- the host-egress contract asserts default deny, restricted DNS, and the same
  exact `/32` database targets present in the private SSH config.

Start with `config.ssh.example.yaml`,
`read-only-env-contract.example.json`, and
`host-egress-attestation.example.json`; the all-zero example hashes are
rejected. Hash the exact reviewed JSON bytes and place those SHA-256 values in
the private YAML. Materialize with the project virtual environment:

```sh
futureagi/.venv/bin/python \
  deploy/dev/fi-select-qualifier/materialize_ssh.py \
  --bundle /private/reviewed-fi-bundle \
  --config /private/fi-dev-ssh.yaml \
  --ssh-config "$HOME/.ssh/config" \
  --known-hosts "$HOME/.ssh/known_hosts" \
  --read-only-env-contract /private/read-only-env-contract.json \
  --egress-attestation /private/host-egress-attestation.json \
  --shard whatfix \
  --run-id fi-dev-qualification-001 \
  --frozen-end 2026-08-15T12:00:00Z \
  --output /private/whatfix-host-plan.json
```

The plan deliberately records `launch_authorized: false` and the live checks
that remain. A human must revalidate reachability with strict host-key checking,
the remote env-file mode and contract, the derived image digest, active host
firewall policy, and all three server-side read-only identities before directly
executing the JSON `container_argv` vector on that host. A saved alias or a
successful SSH handshake alone is not readiness evidence.
