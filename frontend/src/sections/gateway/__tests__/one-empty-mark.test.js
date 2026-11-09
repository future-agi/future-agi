import { describe, expect, it } from "vitest";

import { EMPTY } from "../utils/formatters";
import { EMPTY_TEXT } from "../logs/columns/formatMetadataValue";
import { lineOf, sources } from "./source-scan";

const DIRS = [
  "sections/gateway",
  "sections/settings/BillingV2/gateway-credits",
  "sections/settings/UsageSummaryV2/GatewayUsageSplit.jsx",
];

// A value shown as missing: "N/A", "--", an en dash, or a hyphen as the
// fallback of ??, ||, a ternary, a return or a JSX text node.
const OTHER_EMPTY_MARKS = [
  /"N\/A"/g,
  /"--"/g,
  /"–"/g,
  /(?:\?\?|\|\||return|:)\s*"-"/g,
  /\?\s*"-"\s*:(?!\s*"")/g,
  /=\s*"-"\s*;/g,
  />\s*-\s*<\//g,
];

function scan(patterns, { skipComments = false } = {}) {
  const offenders = [];
  for (const [file, text] of sources(DIRS)) {
    for (const pattern of patterns) {
      for (const match of text.matchAll(pattern)) {
        const line = text.split("\n")[lineOf(text, match.index) - 1];
        if (skipComments && /^\s*(\/\/|\*|\/\*)/.test(line)) continue;
        offenders.push(`${file}:${lineOf(text, match.index)} ${match[0]}`);
      }
    }
  }
  return offenders;
}

// Empty values read "N/A" in the log
// drawer, "-" in the Logs and Sessions grids and "—" on Overview and Keys;
// the brand read "FutureAGI" in the drawer and on the Catalog card and
// "Future AGI" in the product switcher.
describe("one empty mark, one brand spelling", () => {
  it("marks a missing value with the em dash constant", () => {
    expect(EMPTY).toBe("—");
    expect(EMPTY_TEXT).toBe(EMPTY);
  });

  it("no gateway source writes N/A, --, an en dash or a hyphen for a missing value", () => {
    expect(scan(OTHER_EMPTY_MARKS)).toEqual([]);
  });

  it("spells the brand Future AGI in every gateway string", () => {
    expect(scan([/FutureAGI/g], { skipComments: true })).toEqual([]);
  });
});
