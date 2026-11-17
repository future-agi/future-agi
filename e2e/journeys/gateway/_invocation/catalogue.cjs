"use strict";
// No module evaluation of packet specs, no fixture execution or service I/O.
const catalogue = [
  ...require("./catalogue-ops.cjs"),
  ...require("./catalogue-money.cjs"),
  ...require("./catalogue-surfaces.cjs"),
  ...require("./catalogue-composed-money.cjs"),
  ...require("./catalogue-composed-surfaces.cjs"),
  ...require("./catalogue-composed-ops.cjs"),
];
// Within each spec preserve declaration order. Across specs use this explicit
// source inventory, never filesystem order or a W20/tag-only selector.
const specCounts = Object.freeze({
  "journeys/gateway/J27-L3/oracle-controls.spec.ts": 13,
  "journeys/gateway/J27-L4/reporting-controls.spec.ts": 28,
  "journeys/gateway/J27-L5/rollback.spec.ts": 32,
  "journeys/gateway/J27-L6/oracle-controls.spec.ts": 18,
  "journeys/gateway/J11/oracle-controls.spec.ts": 24,
  "journeys/gateway/J12/money-oracle.spec.ts": 30,
  "journeys/gateway/J13/oracle-controls.spec.ts": 60,
  "journeys/gateway/J14/metadata-controls.spec.ts": 30,
  "journeys/gateway/J15/export-detectors.spec.ts": 10,
  "journeys/gateway/J15/guardrail-detectors.spec.ts": 6,
  "journeys/gateway/J15/pricefeed-stock.spec.ts": 1,
  "journeys/gateway/J29/strict-oracle-controls.spec.ts": 42,
  "journeys/gateway/J31/oracle-controls.spec.ts": 24,
  // Cumulative integration additions. Preserve the original 318 IDs/order above.
  "journeys/gateway/J11/stream-controls.spec.ts": 24,
  "journeys/gateway/J12/identity-controls.spec.ts": 15,
  "journeys/gateway/J14/local-fixtures.spec.ts": 8,
  "journeys/gateway/J14/mcp-negotiation.spec.ts": 2,
  "journeys/gateway/J14/revoke-controls.spec.ts": 3,
  "journeys/gateway/J15/sinks-catalogue-fixtures.spec.ts": 12,
  // Exported registrars only: no top-level call exists in this source tree.
  // Their future binding must revise discovery; zero here never means row PASS.
  "journeys/gateway/J27-L3/redis-recovery.spec.ts": 0,
  "journeys/gateway/J27-L4/gateway-lifecycle.spec.ts": 0,
  "journeys/gateway/J27-L6/registration.spec.ts": 1,
  "journeys/gateway/J27-L6/temporal-fixtures.spec.ts": 32,
  "journeys/gateway/J31/catalogue-source.spec.ts": 3,
  "journeys/gateway/_fixtures/money/cancellation-contract.spec.ts": 27,
  "journeys/gateway/_fixtures/money/cap-state.spec.ts": 3,
  "journeys/gateway/_fixtures/money/gemini-contract.spec.ts": 11,
  "journeys/gateway/_fixtures/money/packet-fixtures.spec.ts": 2,
  "journeys/gateway/_fixtures/money/provider-contract.spec.ts": 12,
  "journeys/gateway/_fixtures/redis-lifecycle/lifecycle.spec.ts": 21,
  "journeys/gateway/_fixtures/redis-lifecycle/probe.spec.ts": 12,
});
const files = Object.keys(specCounts);
catalogue.sort((a, b) => files.indexOf(a.file) - files.indexOf(b.file));
if (
  catalogue.length !== 506 ||
  new Set(catalogue.map((x) => x.id)).size !== 506 ||
  new Set(catalogue.map((x) => `${x.file}\0${x.title}`)).size !== 506 ||
  catalogue.some((x) => !files.includes(x.file)) ||
  files.some(
    (file) =>
      catalogue.filter((x) => x.file === file).length !== specCounts[file],
  )
) {
  throw new Error(
    "F01 source catalogue drift; review the exact cumulative specification declarations",
  );
}
// Defensive deep freeze: imported callers cannot expand fullRows or make DARK
// entries executable by mutating a shared exported record before compilation.
function freeze(value) {
  if (value && typeof value === "object") {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
freeze(catalogue);
module.exports = { catalogue, specCounts };
