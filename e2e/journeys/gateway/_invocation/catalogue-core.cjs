"use strict";
// Inventory only: NEVER import a packet spec (that registers/executes Playwright).
// Authority: f93d4fe46437975cba409df92169eb0f6848143d packet spec declarations.
// Product/source tuple remains da4c81cc623f909ce38f53e01b557ee8374d5adb.
const PHASES = [
  "baseline",
  "negative",
  "red",
  "restored",
  "inline-control-sequence",
  "supplemental",
];
function entry(packet, spec, title, phase, options = {}) {
  if (!PHASES.includes(phase)) throw new Error("Unknown inventory phase");
  const kind = options.kind || "detector-only";
  if (
    ![
      "detector-only",
      "fixture-only",
      "product-fragment",
      "admission-guard",
      "runtime-row",
    ].includes(kind)
  )
    throw new Error("Unknown inventory kind");
  const file = options.file || `journeys/gateway/${packet}/${spec}`;
  const browserContextUsed =
    options.browserContextUsed ?? kind === "product-fragment";
  const network =
    options.network || (kind === "product-fragment" ? "own-host" : "none");
  if (
    !["none", "own-host"].includes(network) ||
    (browserContextUsed && network !== "own-host")
  )
    throw new Error("Invalid source inventory network/browser requirements");
  const relatedRows = options.rows || [];
  const dark =
    packet === "J13" && relatedRows.some((row) => row.startsWith("W03-"));
  return Object.freeze({
    id: `${packet}.${spec.replace(".spec.ts", "")}.${options.key || title}`,
    packet,
    file,
    title,
    phase,
    kind,
    // Phase is the native test's phase, not a fabricated separate invocation.
    // Inline detectors (L3/J15) contain their own green/defect/restored steps.
    control: options.control || "",
    relatedRows,
    fullRows: [],
    disposition: dark
      ? "DARK"
      : ["admission-guard", "runtime-row"].includes(kind)
        ? "OWED"
        : "SOURCE_ONLY",
    expectedStatus:
      kind !== "admission-guard" && phase === "red" ? "failed" : "passed",
    annotationTypes: options.annotations || [],
    steps: options.steps || [],
    failureMarker: options.failureMarker || "",
    fixture:
      options.fixture ||
      `${packet}/${spec}: synthetic values inside test callback; no product identity`,
    browserContextUsed,
    network,
    requirements: options.requirements || [],
    profile: kind === "product-fragment" ? "stock" : "none",
    stack: kind === "product-fragment" ? "standalone" : "none",
    width: browserContextUsed ? "REQUIRES_ADMITTED_WIDTH" : "NOT_APPLICABLE",
    tenantIdentity:
      kind === "product-fragment"
        ? "anonymous; no tenants or keys"
        : kind === "admission-guard"
          ? "none; admission fails before fixtures"
          : "synthetic IDs only; not provisioned tenants",
    provenance:
      options.provenance ||
      `f93d4fe46437975cba409df92169eb0f6848143d:e2e/${file}`,
  });
}
const journey = (packet, id, phase) => `GW-JOURNEY-${packet}.${id} [${phase}]`;
module.exports = { entry, journey };
