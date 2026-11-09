"use strict";
const { entry, journey } = require("./catalogue-core.cjs");
const entries = [];
// J14/metadata-controls.spec.ts: exact titles and partial metadata only.
for (const [row, controls] of [
  [
    "W07.1",
    [
      "rotate-version",
      "revoke-status",
      "extra-credential",
      "unauthorized-write",
    ],
  ],
  ["W11.1", ["args-binding", "decider-binding", "extra-pending-row"]],
  ["W07.2", ["duplicate-call", "wrong-via", "missing-call"]],
])
  for (const control of controls)
    for (const phase of ["baseline", "red", "restored"]) {
      entries.push(
        entry(
          "J14",
          "metadata-controls.spec.ts",
          `J14-ORACLE ${row} ${control} [${phase}]`,
          phase,
          {
            rows: [row],
            control,
            annotations: ["journey-subassertion"],
            steps: [
              `${row}/oracle/${control === "unauthorized-write" ? "no-write" : control}/${phase}`,
            ],
            failureMarker:
              row === "W11.1"
                ? "W11.1 exact run-scoped approval audit"
                : row === "W07.2"
                  ? "J14 exact request-cohort tool-call rows"
                  : {
                      "rotate-version": "W07.1 rotate exact cohort",
                      "revoke-status": "W07.1 revoke exact cohort",
                      "extra-credential": "W07.1 create exact cohort",
                      "unauthorized-write":
                        "W07.1 forbidden write leaves metadata unchanged",
                    }[control],
            fixture:
              "J14/metadata-controls.spec.ts:identities; synthetic metadata, no OAuth or role provisioning",
          },
        ),
      );
    }

// J15 detector specs deliberately use inline toThrow checks, not test.fail.
// An invocation must pass; splitting out imagined red tests would select zero.
for (const id of [
  "missing-current",
  "old-record",
  "foreign-record",
  "extra-repeat",
]) {
  entries.push(
    entry(
      "J15",
      "export-detectors.spec.ts",
      `J15-DETECTOR W17.R2 ${id}: exact export multiplicities`,
      "inline-control-sequence",
      {
        rows: ["W17.A03"],
        control: "W17.R2",
        annotations: ["journey-fragment", "journey-oracle-control"],
        steps: [
          "oracle baseline: full multiset including valid repeats",
          `oracle negative: ${id} kills W17.A03 detector only`,
          "oracle restored: unchanged detector, original observation",
        ],
      },
    ),
  );
}
for (const id of [
  "false-success",
  "duplicate-dispatch-row",
  "wrong-count",
  "wrong-kind",
  "missing-final",
  "foreign-connection",
]) {
  entries.push(
    entry(
      "J15",
      "export-detectors.spec.ts",
      `J15-DETECTOR W17.R1 ${id}: exact recovery history`,
      "inline-control-sequence",
      {
        rows: ["W17.A02"],
        control: "W17.R1",
        annotations: ["journey-fragment", "journey-oracle-control"],
        steps: [
          "oracle baseline: exact four-run history",
          `oracle negative: ${id} kills its exact detector`,
          "oracle restored: full original history",
        ],
      },
    ),
  );
}
for (const id of [
  "wrong-status",
  "wrong-action",
  "missing-trigger",
  "missing-request",
  "duplicate-request",
]) {
  entries.push(
    entry(
      "J15",
      "guardrail-detectors.spec.ts",
      `J15-DETECTOR W18.R1 ${id}: guardrail log projection`,
      "inline-control-sequence",
      {
        rows: ["W18.A02"],
        control: "W18.R1",
        annotations: ["journey-fragment", "journey-oracle-control"],
        steps: [
          "oracle baseline: exact passing and blocked logs",
          `oracle negative: ${id} kills its intended assertion`,
          "oracle restored: original pair, identical detector",
        ],
      },
    ),
  );
}
entries.push(
  entry(
    "J15",
    "guardrail-detectors.spec.ts",
    "J15-DETECTOR W18.R1 stored-only-executed: no guardrail metadata",
    "inline-control-sequence",
    {
      rows: ["W18.A03"],
      control: "W18.R1",
      annotations: ["journey-fragment", "journey-oracle-control"],
      steps: [
        "oracle baseline: no guardrail metadata on stored-only call",
        "oracle negative: a stored-only execution witness fails",
        "oracle restored: no metadata again, identical detector",
      ],
    },
  ),
);
entries.push(
  entry(
    "J15",
    "pricefeed-stock.spec.ts",
    journey("J15", "W05-STOCK-ABSENCE-FRAGMENT", "baseline"),
    "baseline",
    {
      kind: "product-fragment",
      rows: ["W05.A01"],
      annotations: ["journey-fragment"],
      steps: ["J15/W05.A01/anonymous-stock-profile-only (partial; NOT_WALKED)"],
      fixture:
        "J15/pricefeed-stock.spec.ts: base page + GatewayAdmin.open; anonymous stock/standalone, no actor",
    },
  ),
);

// J29/strict-oracle-controls.spec.ts. Its synthetic 320px values are NOT an
// actual browser width, nor a substitute for main's complete admitted matrix.
for (const [id, row, control, failure] of [
  [
    "document-overflow",
    "J29-GW-A02",
    "C_MATRIX",
    "document horizontal overflow",
  ],
  ["body-overflow", "J29-GW-A02", "C_MATRIX", "body horizontal overflow"],
  [
    "wrong-viewport",
    "J29-GW-A02",
    "C_MATRIX",
    "the spec must not replace the runner viewport",
  ],
  [
    "ancestor-horizontal-clip",
    "J29-GW-A02",
    "C_MATRIX",
    "control right edge clipped by ancestor",
  ],
  [
    "ancestor-vertical-clip",
    "J29-GW-A02",
    "C_MATRIX",
    "control bottom edge clipped by ancestor",
  ],
  [
    "ancestor-sideways-scroll-before",
    "J29-GW-A02",
    "C_MATRIX",
    "ancestor must not require sideways scrolling",
  ],
  [
    "ancestor-sideways-scroll-after",
    "J29-GW-A02",
    "C_MATRIX",
    "ancestor must not require sideways scrolling",
  ],
  [
    "missing-font",
    "J29-GW-A08",
    "C_NONLOCAL_AND_ASSETS",
    "missing, duplicate or failed Latin Inter face",
  ],
  [
    "failed-font",
    "J29-GW-A08",
    "C_NONLOCAL_AND_ASSETS",
    "missing, duplicate or failed Latin Inter face",
  ],
  [
    "duplicate-font",
    "J29-GW-A08",
    "C_NONLOCAL_AND_ASSETS",
    "missing, duplicate or failed Latin Inter face",
  ],
  [
    "missing-icon",
    "J29-GW-A08",
    "C_NONLOCAL_AND_ASSETS",
    "exact icon multiset",
  ],
  [
    "empty-glyph",
    "J29-GW-A08",
    "C_NONLOCAL_AND_ASSETS",
    "empty SVG is not a usable icon",
  ],
  [
    "hidden-icon",
    "J29-GW-A08",
    "C_NONLOCAL_AND_ASSETS",
    "hidden or transparent icon",
  ],
  ["extra-icon", "J29-GW-A08", "C_NONLOCAL_AND_ASSETS", "exact icon multiset"],
])
  for (const phase of ["baseline", "red", "restored"])
    entries.push(
      entry(
        "J29",
        "strict-oracle-controls.spec.ts",
        journey("J29", `oracle-${id}`, phase),
        phase,
        {
          rows: [row],
          control,
          annotations: ["journey-oracle"],
          steps: [`J29/${row}/${id}/${phase}`],
          failureMarker: `J29: ${failure}`,
          fixture:
            "J29/strict-oracle-controls.spec.ts: synthetic layout/controlGeometry/font/icon; no DOM/browser",
        },
      ),
    );
module.exports = entries;
