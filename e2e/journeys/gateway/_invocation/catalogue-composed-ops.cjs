"use strict";
const { composed } = require("./catalogue-composed-core.cjs");
const { journey } = require("./catalogue-core.cjs");
const entries = [];
// This top-level product-row declaration IS discoverable. Do not disguise it as
// a fixture/detector, run its unbound Temporal port or accept its full-row events
// through F01's source-only reporter. Its adapter/row reconciliation are OWED.
entries.push(
  composed(
    "J27-L6",
    "J27-L6/registration.spec.ts",
    journey("J27-L6", "registration.full-inventory", "baseline"),
    "baseline",
    {
      kind: "runtime-row",
      rows: ["A02"],
      annotations: ["journey-assertion"],
      network: "own-host",
      steps: ["J27-L6/A02/baseline"],
      fixture:
        "F08 temporalRuntime option has no default executor; isolated native registration and fresh schedule reads required",
      requirements: [
        "accepted E6/F08-ABI runtime port",
        "F17/F19 namespace/topology/deadline admission",
        "full-row native event reconciliation; not the source-only reporter",
      ],
    },
  ),
);
const temporal = (id) =>
  entries.push(
    composed(
      "J27-L6",
      "J27-L6/temporal-fixtures.spec.ts",
      journey("J27-L6", `F08.${id}`, "baseline"),
      "baseline",
      {
        annotations: ["journey-fixture-contract"],
        fixture:
          "data-only synthetic Temporal ports and captured schedule-describe JSON; no native registration/worker execution",
      },
    ),
  );
for (const mode of ["peerdb", "outbox"]) temporal(`complete-inventory.${mode}`);
temporal("capture-and-wide-counter");
for (const defect of [
  "duplicate",
  "missing",
  "extra",
  "queue",
  "activity",
  "workflow-id",
])
  temporal(`inventory-rejects.${defect}`);
temporal("list-preserves-multiplicity");
for (const defect of [
  "unsafe-counter",
  "negative-counter",
  "missing-action",
  "bad-payload",
  "bad-paused",
])
  temporal(`capture-rejects.${defect}`);
for (const topology of ["standalone", "distributed"])
  temporal(`registration.${topology}`);
temporal("fixture-no-default-or-reregistration");
temporal("binding-refuses-unadmitted-and-remote");
for (const defect of [
  "missing-type",
  "duplicate",
  "foreign",
  "stale",
  "future",
  "pre-resume",
])
  temporal(`pollers.${defect}`);
for (const cachedTypes of [[1], [2], [1, 2]])
  temporal(`pollers.same-resume-second.${cachedTypes.join("-")}`);
for (const id of [
  "recovery-keeps-worker-lineage",
  "workflow-status-is-not-history",
  "pagination-and-fresh-read-required",
  "poller-projection-fails-closed",
])
  temporal(id);

const lifecycle = (title) =>
  entries.push(
    composed(
      "J27-L4",
      "_fixtures/redis-lifecycle/lifecycle.spec.ts",
      title,
      "inline-control-sequence",
      {
        fixture:
          "scripted in-memory Redis/lifecycle ports; no native operations or sockets",
      },
    ),
  );
for (const name of [
  "wrong-redis",
  "overlap",
  "expired-reservation",
  "nonlocal",
  "wrong-source",
  "dirty-source",
  "missing-require-redis",
  "hidden-standby",
])
  lifecycle(`F03 ${name} refuses before mutation; pristine binding restores`);
lifecycle(
  "F03 unavailable Redis is explicit; a reader error is not outage evidence",
);
lifecycle(
  "F03 preservation requires both FLUSH names, native continuous coverage and no reseed",
);
lifecycle(
  "F03 ID-bound stop, explicit unavailable read and same-volume restore",
);
for (const defect of [
  "wrong-volume",
  "failed-raw-wait",
  "missing-namespace",
  "gateway-restarted",
  "late-recovery",
])
  lifecycle(
    `F03 ${defect} receipt rejected; original synthetic lifecycle restores`,
  );
for (const defect of [
  "drain-refused",
  "unaccounted-run",
  "state-drift",
  "wrong-predecessor",
  "early-restart",
])
  lifecycle(
    `F03 ${defect} refuses before gateway mutation; unchanged recipe restores`,
  );
const probe = (title) =>
  entries.push(
    composed(
      "J27-L3",
      "_fixtures/redis-lifecycle/probe.spec.ts",
      title,
      "inline-control-sequence",
      {
        fixture:
          "data-only Redis read-plan/decoder inputs; no Redis client or sockets",
      },
    ),
  );
probe("F02 read plan pins exact org keys and never requests credentials");
probe(
  "F02 lossless amounts, retained history distinct from pending, explicit missing marker",
);
for (const name of [
  "fractional-money",
  "partial-account",
  "duplicate-hold",
  "unpaired-expiry",
  "missing-marker",
  "duplicate-scope",
  "wrong-marker",
  "missing-marker-positive-ttl",
  "negative-counter",
])
  probe(`F02 decoder rejects ${name}; untouched original restores`);
probe("F02 absent consumer group and unknown lag do not assert drained");
module.exports = entries;
