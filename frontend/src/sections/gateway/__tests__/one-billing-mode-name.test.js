import { describe, expect, it } from "vitest";

import { GATEWAY_DIRS, lineOf, sources } from "./source-scan";

// The same two billing modes had a different name on
// almost every screen (Try it's "Managed credit / Your key", the Filters
// select's "Managed (Future AGI credit) / Your provider key", the Logs
// cell's "BYOK", MCP's "Own credential", Usage Summary's "Your own provider
// keys", the raw "Billing: managed" chip, "Cost by Billing"). One pair now:
// "Managed" / "Own key", from utils/providerLabel.js (BILLING_MODE_LABELS,
// billingModeLabel), under the dimension's name "Billing mode". A managed
// call's provider still reads "Future AGI managed": that is the provider, not the billing mode.
const DIRS = [...GATEWAY_DIRS, "sections/settings/UsageSummaryV2"];

const RETIRED = [
  ["Managed (Future AGI credit)", /Managed \(Future AGI credit\)/],
  ["Your provider key", /["'`>]\s*Your provider key/],
  ["Own credential", /Own credential/],
  ["Billed to", /["'`>]\s*Billed to\b/],
  ["Managed credit (as a mode's name)", /["'`]Managed credit["'`,]/],
  ["BYOK (as a label)", /:\s*["']BYOK["']/],
  ["Your key", /["'`>(]\s*[Yy]our key\b(?!s)/],
  ["Managed + your key", /Managed \+ your key/],
  ["Your own provider keys (as a figure)", /label="Your own provider keys"/],
  ["Managed credits (as a figure)", /label="Managed credits"/],
  [
    "Billing (as the billing-mode dimension)",
    /billing_mode",\s*label:\s*"Billing"/,
  ],
  ["Billing (as the billing-mode chip)", /billingMode:\s*"Billing"/],
];

describe("one name per billing mode", () => {
  it("no screen uses a retired name for managed or own-key billing", () => {
    const offenders = [];
    for (const [file, text] of sources(DIRS)) {
      for (const [name, pattern] of RETIRED) {
        const re = new RegExp(pattern.source, "g");
        let match;
        while ((match = re.exec(text))) {
          offenders.push(`${file}:${lineOf(text, match.index)} ${name}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
