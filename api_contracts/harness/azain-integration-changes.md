# Hosted Harness — Change Requests for Azain (2026-08-25)

From: Khushal (Track B — guest-side execution). Basis: two read-only
reconnaissance passes over `feat/alk-hosted-harness-platform-azain`
(platform) and `feat/hosted-harness-e2e-runtime-azain` (ALK), checked
against the seam contracts (spine v1.14, outbound-channels v1.3),
**updated for your latest pushes** (platform `59c6df115`, alk
`4c35e9d` — P5, P6, and the A2 rewrite below are new). Full evidence
with file:line quotes: `integration-recon-platform.md` and
`integration-recon-alk.md` (ask me for copies). I'll send the
contract files themselves.

## First, what needs NO change (verified working)

- **`runtime.parallelism` is written into job.json verbatim.** This was
  our top integration fear; it is retired. No need to touch it.
- **Delete-and-verify (§0.8)** is exactly right — hard refusal to
  finalize without `verified_absent=True`.
- **Cancel (§0.7)** — cancel-file + SIGTERM + wait — is implemented,
  with one caveat worth a look: `cancel()`'s own polling loop never
  classifies the final exit code, so a guest that cannot flush a
  terminal event inside the window is recorded FAILED in the UI rather
  than canceled-with-partial-evidence. (The cancel-file *path* moved
  in `59c6df115` — covered under P5.)
- **All four v1.3 endpoints exist** and match the wire contract
  (`futureagi/simulate/views/hosted_harness.py`); the two small items
  below (P2, P4) are a contract gap and a canonicalization edge, not
  redesigns.
- **Exit codes 0 / 3 / other** are handled correctly, including the new
  exit 4 without any change needed (see P3).
- **Snapshot users and engine catalog already match**: the
  `svc-control/agent/tools/data` users your Dockerfile creates at
  uid 2000–2003, and the §2b engine set, match the spine.

## Platform side

### P1 — mid-run retries do not exist (`RETRY_WAIT` is dead code)

`HostedHarnessJob.State.RETRY_WAIT` is defined but never set or read.
The gateway launches each attempt exactly once; the Temporal retry
policy only covers failures during *launch*. Consequence: a guest that
dies of an infrastructure failure after reaching RUNNING never gets
attempt 2 — `retry.max_infrastructure_attempts` currently does nothing
for the failure mode it exists to handle. This doesn't block the happy
path; it removes recovery — the difference between a transient flake
costing 90 seconds and costing the demo.

Required behavior (spine §0.6 + §1 `retry`): exit `0` → terminal
reached, read the result from the event stream, no retry. Exit `3` →
attempt superseded, never retried. Any other non-zero (including `4`)
→ infrastructure failure: relaunch a fresh attempt with fresh channels
when the failure's domain is in `retry.retryable_domains`, while
`retry.max_infrastructure_attempts` allows.

### P2 — DECISION: new-vs-duplicate status codes on receipts/manifest

Your receipts and manifest endpoints return `200` for both a new write
and a duplicate replay, distinguishing by a body key. The contract pins
the `201`-new / `200`-replay split **only for artifact upload** (which
you implement) and is silent on the new-write status for the other two
— so your endpoints are conformant as-is; the guest's
`already_existed = (status == 200)` reading is an inference from your
artifact-endpoint precedent. One of three cheap moves: these endpoints
adopt `201`-on-new, or the guest reads the body key, or the contract
pins the status. We're happy to take the guest-side change. Decide at
the call.

### P3 — FYI, zero behavior change: exit code 4

Spine v1.14 added exit `4` = the job reached a terminal state but could
not deliver the terminal event (events channel died, or the platform
rejected the terminal, on the final drain). Your handling is already
correct — anything non-0/non-3 is an infrastructure failure and, once
P1 lands, retried; your new `guest_log_tail` capture in `59c6df115`
(good addition — as is the serializer nested-defaults fix) makes those
crashes diagnosable. Worth adding only a diagnostic label so an exit-4
records as "evidence undeliverable" rather than `guest_crashed` in
operator logs.

### P4 — Two one-liners worth folding in

- **NaN canonicalization**: `canonical_json_bytes`
  (`services/hosted_harness.py`) omits `allow_nan=False`, and the
  score `FloatField(min_value=0.0, max_value=1.0)` does not reject
  `NaN` (`nan < 0` and `nan > 1` are both False) — so a `score: NaN`
  canonicalizes into non-RFC-8259 bytes. The contract makes
  `allow_nan=False` explicit; one line each side.
- **`expires_at` semantics question**: the contract wants
  `expires_at ≥ sandbox TTL + flush window (120s) + 300s`. The code
  sets `ttl_minutes = ceil((max_duration + 120)/60)` and
  `expires_at = now + max_duration + 420`. Against the padded sandbox
  lifetime the contract's floor is `max_duration + 540`, so the token
  expires 120s earlier than the contract asks — 300s of margin past
  sandbox death instead of 420s. Against `sandbox TTL = max_duration`
  it is exact. Which did you mean "sandbox TTL" to denote?

### P5 — NEW, INTEGRATION BREAKER AT MERGE: the control files moved off-contract in `59c6df115`

The gateway now writes `secrets.json`, `capabilities.json`, the cancel
file, and the entrypoint-command-id under **`/work/.harness/`**, no
longer creates `/run/futureagi` or `/work/artifacts`, and sets `/work`
itself to `0700 svc-control`. The contract (§0.3/§0.4/§0.7) places
these at `/run/futureagi/` — and that is where the new guest
entrypoint reads them (it arrives with tonight's branch push; the
branch you can fetch right now still shows the old delegator). Today
this is self-consistent (your sink reads the new paths), but the
moment that push lands, gateway and guest miss each other completely:
no capabilities file found, no cancel signal seen.

Two asks:
1. **What broke with `/run/futureagi`?** If `/run` is missing or
   read-only in the snapshot image, that's a snapshot fix (our Daytona
   spike observed sandbox exec running as root, so `install -d` should
   work) — tell us and we'll help debug. If the move was convenience, please move back: the
   contract chose `/run/futureagi` deliberately, to keep the attempt
   bearer and resolved secrets **outside the `/work` tree that
   customer code executes in**.
2. `/work` at `0700 svc-control` also conflicts with §0's multi-user
   model (the `svc-agent/tools/data` processes must traverse `/work`);
   fine for the current single-user stopgap, needs relaxing per §0
   when the guest lands.

### P6 — NEW: the egress inversion in `59c6df115` needs a semantics check

`network_block_all=True` became `network_block_all=not allowed_domains`
— i.e. whenever any allowlist exists, block-all is now off. Whether
Daytona then enforces allowlist-only egress, or treats the list as
advisory and leaves egress open, decides whether this is a fix or a
hole. Please confirm which semantics Daytona applies (and if this was
the bug from testing — the allowlist not taking effect under
block-all — that's exactly the kind of platform behavior worth a
sentence in the contract). The registry-hosts item under the podman
section reads on top of whatever the answer is.

## ALK side

### A1 — Remove Docker from the demo snapshot

The branch bakes Docker into the snapshot. Three reasons to take it
out:

1. **The demo needs no container runtime at all.** The spine's runtime
   is process-based — customer services run as plain OS processes; that
   is what ships tonight. Docker in the snapshot is size and attack
   surface with zero payoff.
2. **It contradicts the frozen spine.** §0's "No Docker inside the
   sandbox" is the foundation of the current architecture — the
   decision your own hosting plan made, and everything guest-side was
   built on it. Reopening it is a contract discussion, not a snapshot
   default.
3. **Our proposed direction for the image milestone is rootless
   podman, not Docker** — see the rationale below. That proposal
   (`image-runtime-amendment-proposal.md`) is a DRAFT that the
   three-way call decides, and you are one of the deciders; the ask
   here is only that the demo snapshot not pre-commit the choice.

### A2 — Two file collisions with the guest stack (merge-order decision)

- `src/fi/simulate/runtime/spec.py`: your `parallelism` field carries
  `le=8`; our working copy is unbounded above. Yours matches the
  contract — spine §1's implementation delta prescribes
  `Field(default=1, ge=1, le=8)` verbatim, and the bound is structural:
  the per-world port formula `15000 + 100·w + ordinal` tops out at
  `15799` for `w=7`, so the reserved band fits at most W=8. We adopt
  your cap at merge; ours was the non-conforming side.
- `src/fi/alk/harness/hosted_entrypoint.py` + your new
  `hosted_sink.py` (`4c35e9d`): first — the sink was the right
  unblock. Our branch wasn't pushed, you needed an e2e path for
  testing, and a batch-forwarder over the old executor got you there.
  Three things about where it goes next:
  1. **Our guest branch replaces this layer wholesale** (push tonight).
     The new `hosted_entrypoint.py` implements the contract natively —
     live streaming with a durable spool, fencing (exit 3), the
     delivery-keyed exit codes (0/4), terminal-event ordering — where
     the sink batch-forwards after the fact and deliberately never
     lets forwarding affect the exit code (its stated design, but the
     opposite of §0.6's contract). At merge we'd ask that ours lands
     as the base, with the sink's forwarding role retiring; anything
     the sink taught you about the platform's validation quirks, we
     want as review notes on our module.
  2. **The sink cites "the v1.6 hosted-harness outbound contract" —
     that's a stale version number.** Current is outbound-channels
     v1.3 on spine v1.14 (the spine moved v1.5→v1.14 today alone, and
     more than one "v1.6" has existed along the way). I'll send the
     current files — please build against those; digest
     canonicalization matches, but event vocabulary, fencing, and exit
     codes have all moved.
  3. **Secrets: wholesale env injection is a stopgap to retire.** The
     guest model routes each secret to the specific process that
     declared the purpose for it (and the provisioner deletes the file
     after load); injecting the full map into the entrypoint's own
     environment hands every secret to everything downstream. Fine for
     the single-user stopgap, not for the multi-user guest.

### A3 — Snapshot: the rabbitmq specifics + two dirs

- **rabbitmq**: ship the standard `rabbitmq-server` distribution with
  the management plugin **present but not enabled by default**. The
  plugin's built-in listener (15672) sits inside the reserved per-world
  band `[15000,15799]` and would alias an allocated port — that is why
  it must be off at boot; the guest writes its own `enabled_plugins`
  and pins the listener to `amqp_port + 10000`. A snapshot without the
  plugin *files* fails every rabbitmq bundle at boot, so
  present-not-enabled is the exact requirement. `rabbitmqadmin` is not
  assumed — the guest seeds over the management HTTP API. (This
  confirms the open decision our side logged on 2026-08-25 about
  rabbitmq baselines; the plugin question is the part you own.)
- **Layout**: `/work/source` and `/run/futureagi` (§0 states
  owners/modes for `/work/source`, `/work/job.json`, and
  `/run/futureagi/{secrets,capabilities}.json`; `cancel.json` is
  gateway-written at cancel time); the guest creates its own
  subdirectories beyond those.

### A4 — Two `job.py` deltas worth a conscious call before merge

- `hosted_parallelism_exceeds_cpu` is enforced unconditionally; spine
  §1 scopes that rule to **voice** jobs. One-line connector gate.
- `hosted_isolation_must_be_dedicated_vm` hard-rejects where the base
  branch coerced `SHARED_RUNNER_PROCESS → DEDICATED_VM`. The contract
  doesn't adjudicate this — a deliberate behavior change is fine, a
  silent one isn't; flagging so it merges consciously.

## Why podman over Docker (for the image milestone, not tonight)

1. **No daemon.** Docker routes everything through `dockerd` — a
   long-lived, root-equivalent daemon inside every sandbox: a single
   point of failure, one more service to supervise, and state that can
   wedge across worlds. Podman is daemonless; every container is a
   direct child process of its spawner. Our world model is process
   supervision (spawn, health-check, kill the process tree, respawn) —
   podman containers slot straight into it, Docker containers belong to
   the daemon and must be managed through its API instead.
2. **No socket, no socket-escape class.** The Docker socket is
   root-equivalent; the sandbox executes untrusted customer code. With
   rootless podman there is no daemon and no privileged socket to
   reach.
3. **Rootless is native — and the substrate is proven.** Podman's
   rootless mode (user namespaces + fuse-overlayfs) is its primary
   design; Docker's rootless mode is a retrofit that keeps the daemon.
   Our 2026-08-25 spike inside a real Daytona sandbox: nested rootless
   confirmed, user namespaces and /dev/fuse present, two-world postgres
   isolation proven, world start ~757ms / reset ~970ms. Scope caveat,
   stated plainly: the spike ran as root from the stock
   `quay.io/podman/stable` image in a scratch sandbox — the *substrate*
   is validated; a custom-snapshot, `svc-control`, egress-restricted
   rerun is the remaining validation.
4. **Networking reality when nested.** Port mapping is broken and
   bridged networking is unreliable (containers can fail at start) in
   the nested setup; host networking with the native per-world port
   bands is the working rule. Rootless podman handles that cleanly;
   Docker adds daemon indirection on top of the same constraint.
5. **Footprint.** Podman stack (podman + crun + conmon +
   netavark/aardvark + fuse-overlayfs + uidmap): roughly **100–150 MB**
   installed. Docker stack (dockerd + containerd + runc + CLI):
   roughly **350–500 MB**. Dropping Docker and later adding podman
   leaves the snapshot smaller than it is today.

**Image-milestone prerequisite in your lane (important):** the spike's
registry-egress result was obtained in a scratch sandbox **outside a
hosted job's egress policy**. Your gateway restricts job egress to an
allowlist (`ALK_HOSTED_BASE_EGRESS_DOMAINS ∪
security.allowed_egress_domains ∪ platform host` — with the
block-all/allowlist mechanics now under discussion in P6), and
registry hosts are not in that list — so under allowlist-enforced
egress, **every image pull in production fails**. When the image
milestone lands, the allowlist needs the registry hosts
(`registry-1.docker.io`, `auth.docker.io`,
`production.cloudflare.docker.com`, or the equivalents for the chosen
registry) — or we pre-bake images instead and skip pulls entirely.
That's the real either/or behind the size numbers below.

## Snapshot size estimate with podman

Current snapshot: ~2 GB (your figure), **Docker included**. The podman
stack itself:

| Component | Installed size (approx.) |
|---|---|
| podman 5.x binary | ~50 MB |
| crun + conmon | ~3 MB |
| netavark + aardvark-dns | ~15–20 MB |
| fuse-overlayfs, pasta/slirp4netns, uidmap, configs | ~5–10 MB |
| shared-library deps not already present | ~10–30 MB |
| **Total** | **~100–150 MB** |

**Estimate under this doc's own plan (drop Docker, add podman):
dropping the Docker stack (−350–500 MB) and adding podman
(+100–150 MB) lands at ~1.65–1.80 GB — a net reduction. If Docker
stayed, ~2.10–2.15 GB.** These are package-footprint figures, not a
measured bake (the spike image shipped podman preinstalled, so no
install delta was recorded); treat ±50 MB as the honest error bar and
measure at bake time.

Two caveats that matter more than the binaries:

- **Pre-baked container images are the real size lever, and they trade
  off against the egress allowlist above.** Zero pre-baked images +
  allowlisted registries = smallest snapshot, pulls at job time.
  Pre-baked bases = no registry dependency, but each image adds its
  unpacked size to the snapshot (unpacked sizes for the candidate
  images are so far unmeasured; ballpark `python:3.12-slim` ~130 MB,
  `postgres:16` ~430 MB — verify before deciding). Our default for
  the size question: zero pre-baked images + the allowlist entries.
  The amendment still has you shipping `prebaked-digests.json` and a
  ≤3 GiB image-store budget (§I.1/§I.3) — pre-pull becomes a
  cold-start optimization on top, once the unpacked sizes are
  measured.
- **Runtime disk is a separate budget from snapshot size.** Pulled
  images and container storage land on the sandbox disk at job time
  (the org cap is 10 GB/sandbox; your create call currently sets no
  disk size, so the effective value comes from the snapshot — worth
  confirming what that resolves to). fuse-overlayfs (confirmed
  available) keeps per-world overhead to copy-on-write deltas; the
  pathological full-copy storage mode (vfs) does not apply.

## Suggested order of work

1. P5 (control-file paths back to `/run/futureagi`, or tell us what
   broke) — hard breaker between your gateway and the guest at merge.
2. P1 (retry loop) — most likely first-live-run saver.
3. P6 (egress semantics confirm) — one answer, security-relevant.
4. A3 rabbitmq confirm + A1 (drop Docker from the demo snapshot) — the
   re-bake happens anyway once the final guest branch lands.
5. P2 + A2 + A4 — decisions at the three-way call, small either way.
6. P4 one-liners + P3 label — whenever convenient.

---

## Appendix — execution notes (for the implementing agent)

If this document is handed to a coding agent: items are labeled ACTION
(implement now), DECISION (a human picks an option first — do NOT
choose and implement one), or QUESTION (answer goes back to Khushal;
no code change until then). File anchors are on branch
`feat/alk-hosted-harness-platform-azain @ 59c6df115` (platform) and
`feat/hosted-harness-e2e-runtime-azain @ 4c35e9d` (alk); grep the
named symbols rather than trusting line numbers.

- **P1 — ACTION.** Files:
  `futureagi/simulate/temporal/workflows/hosted_harness_gateway_workflow.py`
  (the launch-once workflow), `futureagi/simulate/models/hosted_harness.py`
  (`HostedHarnessJob.State.RETRY_WAIT`, currently unreferenced),
  `futureagi/simulate/services/hosted_harness_gateway.py` (exit-code
  observation). Implement the relaunch loop exactly as specified in P1;
  do not invent retry semantics beyond it. Acceptance: a job whose
  guest exits non-0/non-3 mid-run gets a fresh attempt (new sandbox,
  new capability token) until `retry.max_infrastructure_attempts` is
  spent; exit 3 never relaunches; exit 0 never relaunches; grep shows
  RETRY_WAIT set and read.
- **P2 — DECISION** (three options in the text; pick at the call).
  Anchor if the 201 option wins: `futureagi/simulate/views/hosted_harness.py`
  receipt + manifest handlers.
- **P3 — ACTION (label only).** Anchor: the exit-code observation in
  `hosted_harness_gateway.py`. Acceptance: exit 4 recorded with an
  "evidence undeliverable" label, still counted as infrastructure.
- **P4a — ACTION.** Anchors: `canonical_json_bytes` in
  `futureagi/simulate/services/hosted_harness.py` (add
  `allow_nan=False`); the score `FloatField` in
  `futureagi/simulate/serializers/hosted_harness.py` (reject NaN).
  Acceptance: a `score: NaN` receipt is rejected with a 4xx, not
  canonicalized.
- **P4b — QUESTION** (which quantity "sandbox TTL" denotes). Anchor
  for whoever answers: `_TOKEN_TAIL_SECONDS` and `expires_at` in
  `futureagi/simulate/services/hosted_harness.py`, `ttl_seconds` in
  `hosted_harness_gateway.py`. No code change until answered.
- **P5 — QUESTION first, then ACTION.** The question: what broke with
  `/run/futureagi`. If nothing structural: revert the path changes in
  `hosted_harness_gateway.py` (secrets/capabilities/cancel/command-id
  back to `/run/futureagi/`, restore `/work/artifacts` +
  `/run/futureagi` creation, `/work` permissions per §0's multi-user
  model). Do NOT instead change the guest or the contract to
  `/work/.harness` — the placement is a deliberate security boundary.
  Acceptance: gateway writes match spine §0.3/§0.4/§0.7 paths.
- **P6 — QUESTION** (Daytona's semantics for
  `network_block_all=False` + `domain_allow_list`). No further egress
  changes until answered.
- **A1 — DECISION** (drop Docker from the demo snapshot — recommended,
  but it is a snapshot-content decision, not an unreviewed code edit).
- **A2 — HOLD.** No further edits to
  `src/fi/alk/harness/hosted_entrypoint.py` or new work on
  `hosted_sink.py`; the guest branch replaces this layer. `spec.py`
  `le=8` needs nothing from this side.
- **A3 — ACTION (snapshot).** rabbitmq: management plugin files
  present, NOT enabled by default; no `rabbitmqadmin` needed.
  Acceptance: `rabbitmq-plugins list` in the image shows
  `rabbitmq_management` available and disabled.
- **A4 — DECISION** (both `job.py` deltas: voice-scoping the CPU rule;
  reject-vs-coerce on isolation).

One global rule for the agent: where this document and the current
contract files disagree with local code comments or older contract
copies (anything citing "v1.6"), the current contracts win — spine
v1.14, outbound-channels v1.3. Get them from Khushal before starting
P1.
