"use strict";
const { entry, journey } = require("./catalogue-core.cjs");
const entries = [];
// J27-L3/oracle-controls.spec.ts: ALL titles are baseline, including tests
// which run synthetic defect checks inline. Do not invent red/restored tests.
for (const [id, row] of [
  ["MANAGED-DELIVERY", "L3-A04"],
  ["MCP-DELIVERY", "L3-A05"],
  ["MISSING-HOLD", "L3-A07"],
  ["DUPLICATE-DEBIT", "L3-A07"],
  ["REVIVED-EPOCH", "L3-A08"],
  ["REVIVED-REVOKED", "L3-A08"],
  ["MISSING-SINK", "L3-A04"],
  ["EXTRA-SINK-ROW", "L3-A05"],
  ["DUPLICATE-HOLD-ROW", "L3-A07"],
  ["MISSING-DEBIT-ROW", "L3-A07"],
  ["MISSING-EPOCH-ROW", "L3-A08"],
  ["EMPTY-REQUEST-INVENTORY", "L3-A09"],
  ["MALFORMED-COUNTER", "L3-A04"],
])
  entries.push(
    entry(
      "J27-L3",
      "oracle-controls.spec.ts",
      journey("J27-L3", `L3-C02-UNIT-${id}`, "baseline"),
      "baseline",
      {
        rows: [row],
        control: "L3-C02-ORACLE-FALLIBILITY",
        annotations: ["journey-oracle-support"],
        steps: [
          `L3-C02-ORACLE-FALLIBILITY/${row}/${id}/synthetic-red`,
          `L3-C02-ORACLE-FALLIBILITY/${row}/untouched-synthetic-restored`,
        ],
        fixture:
          "J27-L3/oracle-controls.spec.ts:syntheticInput; no Redis/ledger/tenant reads",
      },
    ),
  );

// J27-L4/reporting-controls.spec.ts registerOracleControls C06/C07.
for (const [control, row, variants] of [
  [
    "C06",
    "L4-A02",
    [
      ["false-no-interruption", "interruption-disclosure"],
      ["two-replicas-before", "before-single-replica"],
      ["two-replicas-after", "after-single-replica"],
      ["dropped-refusal", "refusals-exact"],
      ["wrong-interval", "intervals-exact"],
      ["redundancy-claim", "redundancy-disclosure"],
    ],
  ],
  [
    "C07",
    "L4-A09",
    [
      ["ee-process", "process-roles"],
      ["edge-container", "container-roles"],
      ["non-404", "denials-404"],
      ["missing-census", "UNKNOWN-missing-census"],
      ["incomplete-census", "UNKNOWN-incomplete-census"],
      ["missing-denial", "route-inventory"],
      ["duplicate-denial", "route-inventory"],
      ["wrong-tuple", "denial-tuple"],
    ],
  ],
])
  for (const [id, code] of variants)
    for (const phase of ["red", "restored"]) {
      entries.push(
        entry(
          "J27-L4",
          "reporting-controls.spec.ts",
          journey("J27-L4", `${control}.${id}.oracle`, phase),
          phase,
          {
            rows: [row],
            control,
            annotations: ["journey-oracle-control"],
            steps: [`J27-L4/${row}/${control}/${id}/${phase}/oracle-only`],
            failureMarker: `[J27-L4 oracle ${control}.${code}]`,
            fixture: `J27-L4/reporting-controls.spec.ts:${control === "C06" ? "interruptionFixture" : "absenceFixture"}; synthetic only`,
          },
        ),
      );
    }

// J27-L5/obligations.ts and control-cases.ts drive rollback.spec.ts. These are
// deliberately failing NOT_WALKED admission guards, NEVER red qualification.
const l5 = (id, phase, rows, control = "") =>
  entries.push(
    entry("J27-L5", "rollback.spec.ts", journey("J27-L5", id, phase), phase, {
      kind: "admission-guard",
      rows,
      control,
      annotations: rows.map(() => "journey-owed"),
      steps: [`J27-L5/${id}/${phase}: NOT_WALKED`],
      fixture: "none; notWalkedStep throws before any product act",
    }),
  );
for (const [id, row] of [
  ["L5-EVIDENCE", "L5-A01"],
  ["L5-PAIR", "L5-A02"],
  ["L5-IDENTITY", "L5-A03"],
  ["L5-HOLDS", "L5-A04"],
  ["L5-BUDGETS", "L5-A05"],
  ["L5-EPOCHS", "L5-A06"],
  ["L5-ORDER", "L5-A07"],
  ["L5-SETTLEMENT", "L5-A08"],
  ["L5-RESTORED", "L5-A09"],
  ["L5-SCHEDULE", "L5-A10"],
])
  l5(id, "baseline", [row]);
for (const [id, phase, rows, control] of [
  ["C01-WRONG-DIGEST", "negative", ["L5-A02"], "L5-C01-pair-and-schedule"],
  ["C01-NONADJACENT", "negative", ["L5-A02"], "L5-C01-pair-and-schedule"],
  [
    "C01-MISSING-RESERVATION",
    "negative",
    ["L5-A10"],
    "L5-C01-pair-and-schedule",
  ],
  ["C01-OVERLAP", "negative", ["L5-A10"], "L5-C01-pair-and-schedule"],
  [
    "C01-RESTORED",
    "restored",
    ["L5-A02", "L5-A10"],
    "L5-C01-pair-and-schedule",
  ],
  ["C02-IDENTITY", "red", ["L5-A03"], "L5-C02-redis-identity"],
  ["C02-RESTORED", "restored", ["L5-A03"], "L5-C02-redis-identity"],
  ["C03-HOLD", "red", ["L5-A04"], "L5-C03-state-fallibility"],
  ["C03-HOLD-RESTORED", "restored", ["L5-A04"], "L5-C03-state-fallibility"],
  ["C03-BUDGET", "red", ["L5-A05"], "L5-C03-state-fallibility"],
  ["C03-BUDGET-RESTORED", "restored", ["L5-A05"], "L5-C03-state-fallibility"],
  ["C03-EPOCH", "red", ["L5-A06"], "L5-C03-state-fallibility"],
  ["C03-EPOCH-RESTORED", "restored", ["L5-A06"], "L5-C03-state-fallibility"],
  ["C04-DUPLICATE-DEBIT", "red", ["L5-A08"], "L5-C04-financial-oracle"],
  ["C04-RESTORED", "restored", ["L5-A08"], "L5-C04-financial-oracle"],
  [
    "C05-PREVIOUS-DENIALS",
    "negative",
    ["L5-A05", "L5-A06", "L5-A07", "L5-A08"],
    "L5-C05-real-denial-and-positive",
  ],
  [
    "C05-PREVIOUS-POSITIVE",
    "restored",
    ["L5-A05", "L5-A06", "L5-A07", "L5-A08"],
    "L5-C05-real-denial-and-positive",
  ],
  [
    "C05-CURRENT-DENIALS",
    "negative",
    ["L5-A05", "L5-A06", "L5-A07", "L5-A08", "L5-A09"],
    "L5-C05-real-denial-and-positive",
  ],
  [
    "C05-CURRENT-POSITIVE",
    "restored",
    ["L5-A05", "L5-A06", "L5-A07", "L5-A08", "L5-A09"],
    "L5-C05-real-denial-and-positive",
  ],
  ["C06-RUNTIME-REDS", "red", ["L5-A01"], "L5-C06-inherited-runtime-controls"],
  ["C06-RESTORED", "restored", ["L5-A01"], "L5-C06-inherited-runtime-controls"],
  [
    "FINAL-RESTORED-SEQUENCE",
    "restored",
    ["L5-A09"],
    "L5-C05-real-denial-and-positive",
  ],
])
  l5(id, phase, rows, control);

// J27-L6 is backend detector work; browser fields identify the runner project,
// not browser coverage. No live topology may be inferred from the title suffix.
for (const [id, row] of [
  ["C04.marker-after-recovery", "A05"],
  ["C04.marker-reread", "A05"],
  ["C04.duplicate-debit", "A06"],
])
  for (const phase of ["red", "restored"])
    entries.push(
      entry(
        "J27-L6",
        "oracle-controls.spec.ts",
        journey("J27-L6", id, phase),
        phase,
        {
          rows: [row],
          control: "C04",
          annotations: ["journey-oracle-control"],
          steps: [`J27-L6/${row}/C04/${phase}/oracle-only`],
          fixture:
            "J27-L6/oracle-fixtures.ts:settlementFixture; synthetic IDs only",
        },
      ),
    );
for (const topology of ["standalone", "distributed"])
  for (const [id, row] of [
    ["C05.worker-identity", "A07"],
    ["C05.temporal-identity", "A07"],
    ["C05.disable-witness", "A08"],
  ])
    for (const phase of ["red", "restored"])
      entries.push(
        entry(
          "J27-L6",
          "oracle-controls.spec.ts",
          journey("J27-L6", `${id}.${topology}`, phase),
          phase,
          {
            rows: [row],
            control: "C05",
            annotations: ["journey-oracle-control"],
            steps: [`J27-L6/${row}/C05/${topology}/${phase}/oracle-only`],
            fixture: `J27-L6/oracle-fixtures.ts:topologyFixture(${topology}); synthetic topology, not an actual stack`,
          },
        ),
      );
module.exports = entries;
