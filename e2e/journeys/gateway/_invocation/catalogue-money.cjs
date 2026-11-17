"use strict";
const { entry, journey } = require("./catalogue-core.cjs");
const entries = [];
const phases = ["baseline", "red", "restored"];
// Exact declaration order and discriminator strings from J11/oracle-controls.spec.ts.
for (const [id, control, rows, failure] of [
  ["money-one-micro", "C-MONEY", ["W02-04"], "statement closing"],
  [
    "money-duplicate-debit",
    "C-MONEY",
    ["W01-07", "W02-04"],
    "entries exact multiplicity",
  ],
  ["money-half-up-boundary", "C-MONEY", ["W01-05"], "half-up billed micros"],
  ["money-packet-header", "C-MONEY", ["W01-05"], "packet header equality"],
  ["byok-markup", "C-BYOK", ["W01-08"], "BYOK markup"],
  ["byok-debit", "C-BYOK", ["W01-08"], "BYOK no public debit"],
  ["byok-balance", "C-BYOK", ["W01-08"], "account unchanged"],
  ["readonly-version", "C-READONLY", ["W02-03"], "account unchanged"],
])
  for (const phase of phases)
    entries.push(
      entry(
        "J11",
        "oracle-controls.spec.ts",
        `J11-ORACLE.${id} [${phase}]`,
        phase,
        {
          control,
          rows,
          annotations: [
            "journey-oracle-partial",
            ...(phase === "red" ? ["j11-oracle-intended-failure"] : []),
          ],
          steps: [`${control}/${phase}/${id} (partial detector only)`],
          failureMarker: `J11-ORACLE ${failure}`,
        },
      ),
    );

// J12/money-oracle.spec.ts: six supplemental tests have NO phase suffix/annotation.
for (const [control, ids, rows, step, failure] of [
  [
    "C02",
    [
      "released-obligation",
      "partial-terminal-undercharge",
      "split-partial-obligation",
      "hold-without-fence",
      "one-unit-conservation-loss",
    ],
    ["A02", "A03", "A04", "A05", "A07", "A10"],
    "same missing-usage arithmetic assertion in all three phases",
    "J12 oracle: missing usage must retain full-H settlement or fenced H",
  ],
  [
    "C04",
    ["duplicate-debit", "duplicate-refund", "missing-replay-observations"],
    ["A03", "A06", "A07", "A09", "A10"],
    "same known-usage arithmetic assertion in all three phases",
    "J12 oracle: repeated observations must retain exact known usage",
  ],
])
  for (const id of ids)
    for (const phase of phases)
      entries.push(
        entry(
          "J12",
          "money-oracle.spec.ts",
          `J12-ORACLE.${control}.${id} [${phase}]`,
          phase,
          {
            control,
            rows,
            annotations: ["j12-oracle-control"],
            steps: [step],
            failureMarker: failure,
          },
        ),
      );
for (const id of [
  "funded",
  "reservation",
  "full-hold",
  "fenced-hold",
  "known-usage",
]) {
  entries.push(
    entry(
      "J12",
      "money-oracle.spec.ts",
      `J12-ORACLE.boundary.${id}`,
      "supplemental",
      { steps: ["packet F/H/U exact arithmetic"] },
    ),
  );
}
entries.push(
  entry(
    "J12",
    "money-oracle.spec.ts",
    "J12-ORACLE.integer-units",
    "supplemental",
    {
      steps: [
        "reject floats, exponent notation, partial values and signed zero",
        "retain a one-unit defect beyond JavaScript safe integer range",
      ],
    },
  ),
);

// J13/oracle-cases.ts + oracle-controls.spec.ts. W03 entries remain inventoried
// but DARK/non-dispatchable: F01 neither creates nor enables max_charge fixtures.
for (const [id, family, row] of [
  ["bound-dispatch", "BOUND", "W03-12"],
  ["bound-metadata", "BOUND", "W03-02"],
  ["scope-spent-held", "SCOPE", "W03-05"],
  ["scope-live-pttl", "SCOPE", "W03-07"],
  ["scope-terminal-pttl", "SCOPE", "W03-07"],
  ["scope-same-key", "SCOPE", "W03-08"],
  ["ledger-capped-debit", "LEDGER", "W03-11"],
  ["ledger-waived-anomaly", "LEDGER", "W03-11"],
  ["ledger-upstream-cost", "LEDGER", "W03-11"],
  ["messages-metadata", "LEDGER", "W03-10"],
  ["tenant-result", "LEDGER", "W03-13"],
  ["mixed-refusal", "LEDGER", "W03-14"],
  ["caps-dispatch", "CAPS", "W04-05"],
  ["caps-byok", "CAPS", "W04-06"],
  ["caps-scope", "CAPS", "W04-04"],
  ["caps-utc", "CAPS", "W04-06"],
  ["revision-activation", "REVISION-ROLES", "W04-03"],
  ["revision-increase", "REVISION-ROLES", "W04-03"],
  ["revision-copy", "REVISION-ROLES", "W04-03"],
  ["member-version", "REVISION-ROLES", "W04-07"],
])
  for (const phase of phases)
    entries.push(
      entry(
        "J13",
        "oracle-controls.spec.ts",
        journey("J13", `oracle.${id}`, phase),
        phase,
        {
          rows: [row],
          control: `CTRL-${row.startsWith("W03") ? "W03" : "W04"}-${family}`,
          annotations: ["journey-oracle-control"],
          steps: [`${row}/oracle/${id}/${phase}`],
          failureMarker: `J13/oracle/${id}`,
          fixture:
            "J13/oracle-cases.ts:OracleSeeds; synthetic key/scope IDs only",
        },
      ),
    );

// J31/oracle-controls.spec.ts, including the independent money baseline and
// integer rounding supplemental baseline (neither is an executed money row).
const j31 = (title, phase, row, control, step, marker = "") =>
  entries.push(
    entry("J31", "oracle-controls.spec.ts", title, phase, {
      rows: [row],
      control,
      annotations: ["journey-subassertion"],
      steps: [step],
      failureMarker: marker,
    }),
  );
j31(
  "J31-ORACLE.C02.money [baseline]",
  "baseline",
  "A08",
  "C02",
  "J31/A08/C02 baseline detector only",
);
for (const [id, marker] of [
  ["response-cost", "A08 exact x-agentcc-cost"],
  ["unit-price", "A08 exact x-agentcc-cost"],
  ["request-cost", "A08 request cost in integer micros"],
  ["request-markup", "A08 exact request markup"],
  ["snapshot", "A08 exact request identity and billing source"],
  [
    "duplicate-request-log",
    "A08 exactly one request log, including tombstones",
  ],
])
  for (const phase of ["red", "restored"])
    j31(
      `J31-ORACLE.C02.${id} [${phase}]`,
      phase,
      "A08",
      "C02",
      `J31/A08/C02/${id}/${phase}`,
      marker,
    );
j31(
  "J31-ORACLE.C02.integer-rounding [baseline]",
  "baseline",
  "A08",
  "C02",
  "J31/A08/C02 exact money boundaries, detector only",
);
for (const id of ["extra-router", "same-count-substitution", "duplicate"]) {
  for (const phase of ["red", "restored"])
    j31(
      `J31-ORACLE.C03.${id} [${phase}]`,
      phase,
      "A05",
      "C03",
      `J31/A05/C03/${id}/${phase}`,
      "A05/A06 exact model IDs including multiplicity",
    );
}
for (const id of ["credit-drift", "config-drift"]) {
  for (const phase of ["red", "restored"])
    j31(
      `J31-ORACLE.C06.${id} [${phase}]`,
      phase,
      "A12",
      "C06",
      `J31/A12/C06/${id}/${phase}`,
      "A12 org B observable state unchanged",
    );
}
module.exports = entries;
