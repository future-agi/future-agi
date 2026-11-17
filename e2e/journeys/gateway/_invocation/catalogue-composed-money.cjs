"use strict";
const { composed } = require("./catalogue-composed-core.cjs");
const entries = [];
for (const [packet, spec, annotation, cases] of [
  [
    "J11",
    "stream-controls.spec.ts",
    "journey-oracle-partial",
    [
      [
        "missing-usage",
        "C-USAGE",
        ["W01-04", "W01-06"],
        "usage exact inventory",
      ],
      ["duplicate-usage-row", "C-USAGE", ["W01-06"], "usage exact inventory"],
      [
        "wrong-native-count",
        "C-USAGE",
        ["W01-04", "W01-06"],
        "usage exact inventory",
      ],
      ["foreign-request", "C-ISOLATION", ["W01-09"], "usage exact inventory"],
      [
        "reversed-attempts",
        "C-FALLBACK",
        ["W01-04", "W01-06"],
        "attempts exact order",
      ],
      [
        "missing-fallback",
        "C-FALLBACK",
        ["W01-04", "W01-06"],
        "attempts exact order",
      ],
      ["duplicate-attempt", "C-FALLBACK", ["W01-06"], "attempts exact order"],
      [
        "refusal-dispatch",
        "C-REFUSAL",
        ["W01-02"],
        "refused request no dispatch",
      ],
    ],
  ],
  [
    "J12",
    "identity-controls.spec.ts",
    "j12-oracle-control",
    [
      [
        "dropped-debit",
        "C02",
        ["A02", "A03", "A04", "A05", "A07", "A10"],
        "money exact identities",
      ],
      [
        "duplicate-debit",
        "C04",
        ["A03", "A06", "A07", "A09", "A10"],
        "money exact identities",
      ],
      [
        "compensated-duplicate",
        "C04",
        ["A09", "A10"],
        "money exact identities",
      ],
      ["wrong-request", "C04", ["A10"], "money exact identities"],
      ["retry-fallback-order", "C03", ["A06"], "attempts exact order"],
    ],
  ],
]) {
  for (const [id, control, rows, failure] of cases) {
    for (const phase of ["baseline", "red", "restored"]) {
      entries.push(
        composed(
          packet,
          `${packet}/${spec}`,
          `${packet}-ORACLE.${id} [${phase}]`,
          phase,
          {
            kind: "detector-only",
            control,
            rows,
            annotations: [
              annotation,
              ...(phase === "red" ? ["money-intended-failure"] : []),
            ],
            steps: [`${control}/${id}: unchanged detector`],
            failureMarker: `MONEY-CONTROL ${failure}`,
          },
        ),
      );
    }
  }
}
const fixture = (
  packet,
  spec,
  title,
  phase = "supplemental",
  network = "none",
) =>
  entries.push(
    composed(packet, `_fixtures/money/${spec}.spec.ts`, title, phase, {
      network,
    }),
  );
// Native terminal remains passed for these negative controls: they assert the
// expected refusal inside the callback, not test.fail / an expected native red.
for (const dialect of ["openai", "anthropic", "gemini"]) {
  for (const usage of ["complete", "delayed", "duplicate"]) {
    for (const phase of ["wrong-after-output", "restored-after-usage"]) {
      fixture(
        "J12",
        "cancellation-contract",
        `MONEY-FIXTURE.cancel-boundary.${dialect}.${usage}.${phase}`,
        phase === "wrong-after-output" ? "negative" : "restored",
        "own-host",
      );
    }
    fixture(
      "J12",
      "cancellation-contract",
      `MONEY-FIXTURE.cancel-boundary.${dialect}.${usage}.before-output-oracle`,
      "inline-control-sequence",
    );
  }
}
for (const id of [
  "budget-reader-bound-parameters-no-revision-substitution",
  "budget-reader-refuses-invalid-input-before-query",
  "budget-reader-propagates-storage-failure",
]) {
  fixture("J13", "cap-state", `MONEY-FIXTURE.${id}`);
}
for (const usage of ["complete", "delayed", "duplicate"]) {
  for (const phase of [
    "terminal-consumed",
    "premature-after-output",
    "premature-after-usage",
  ]) {
    fixture(
      "J11",
      "gemini-contract",
      `MONEY-FIXTURE.gemini-adapter.${usage}.${phase}`,
      phase === "terminal-consumed" ? "baseline" : "negative",
      "own-host",
    );
  }
}
fixture(
  "J11",
  "gemini-contract",
  "MONEY-FIXTURE.gemini-partial-forwarding-baseline-dropped-restored",
  "inline-control-sequence",
);
fixture(
  "J11",
  "gemini-contract",
  "MONEY-FIXTURE.gemini-no-terminal-before-usage-barrier",
);
fixture(
  "J11",
  "packet-fixtures",
  "MONEY-FIXTURE.J11-exact-native-program-inventory",
);
fixture(
  "J12",
  "packet-fixtures",
  "MONEY-FIXTURE.J12-exact-fault-program-inventory",
);
for (const dialect of ["openai", "anthropic", "gemini"]) {
  fixture(
    "J11",
    "provider-contract",
    `MONEY-FIXTURE.${dialect}.complete-absent-partial-duplicate`,
  );
  fixture(
    "J11",
    "provider-contract",
    `MONEY-FIXTURE.${dialect}.real-stream-barriers`,
    "supplemental",
    "own-host",
  );
}
for (const id of [
  "cancel-before-after-usage-and-midstream-disconnect",
  "retry-fallback-exact-order-and-no-extra-call",
  "synthetic-BYOK-credential-equality-never-emits-value",
]) {
  fixture(
    "J11",
    "provider-contract",
    `MONEY-FIXTURE.${id}`,
    "supplemental",
    "own-host",
  );
}
for (const id of [
  "native-wire-fields-are-exact",
  "native-and-translated-message-anchor",
  "closed-plans-refuse-invalid-cells",
]) {
  fixture("J11", "provider-contract", `MONEY-FIXTURE.${id}`);
}
module.exports = entries;
