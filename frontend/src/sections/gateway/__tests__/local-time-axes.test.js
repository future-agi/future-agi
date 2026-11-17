import { describe, expect, it } from "vitest";

import { lineOf, sources } from "./source-scan";

// ApexCharts labels a datetime axis, and formats its
// tooltip, in UTC unless told otherwise, while Logs states the viewer's
// zone. Every gateway datetime axis and its tooltip come from
// utils/timeZone.js (localTimeAxis, localTimeTooltip), which plot local time.
const HELPER = "sections/gateway/utils/timeZone.js";

describe("gateway time axes", () => {
  it("every datetime axis is built by localTimeAxis", () => {
    const offenders = [];
    for (const [file, text] of sources()) {
      if (file === HELPER) continue;
      for (const match of text.matchAll(/type:\s*["'`]datetime["'`]/g)) {
        offenders.push(`${file}:${lineOf(text, match.index)}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("no chart tooltip formats its time with Apex's UTC format string", () => {
    const offenders = [];
    for (const [file, text] of sources()) {
      if (file === HELPER) continue;
      for (const match of text.matchAll(/x:\s*\{\s*format:/g)) {
        offenders.push(`${file}:${lineOf(text, match.index)}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
