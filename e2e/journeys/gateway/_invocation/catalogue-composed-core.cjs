"use strict";
const { entry } = require("./catalogue-core.cjs");
// Static declarations from the composed source tree, not a Playwright collector.
// Do not import specs or infer acceptance from their titles/related packet rows.
const SOURCE = "f59b5541cfdbe747fa26007f2c71501e05924d69";
function composed(packet, relativeFile, title, phase, options = {}) {
  const file = `journeys/gateway/${relativeFile}`;
  return entry(packet, relativeFile.split("/").at(-1), title, phase, {
    kind: "fixture-only",
    fixture: `${relativeFile}: fixture qualification only; no product rows or provisioned tenants`,
    ...options,
    file,
    provenance: `${SOURCE}:e2e/${file}`,
  });
}
module.exports = { composed };
