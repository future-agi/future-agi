import { describe, expect, it } from "vitest";
import { fixStepHref } from "../fixStepHref";
import { iterateHref } from "src/sections/common/Analysis/analysisLinks";

describe("fixStepHref in place (L3 G9 fix 9)", () => {
  const back = "/dashboard/develop/ds/compare/tasks?runs=B,A&baseline=B&row=r1";
  it("opens the dock on the cause's rows with its fix drafted", () => {
    const href = fixStepHref({
      lane: { id: "B", experiment_id: "e2" },
      rowId: "r1",
      causes: [
        {
          id: "RC_PREFIX",
          run_id: "B",
          rows: [{ row_id: "r1" }, { row_id: "r2" }, { row_id: "r6" }],
        },
      ],
      baselineId: "A",
      back,
      iterateHref,
    });
    const sp = new URLSearchParams(href.split("?")[1]);
    expect(sp.get("scope")).toBe("rows");
    expect(sp.get("rows")).toBe("r1,r2,r6");
    expect(sp.get("change")).toBe("RC_PREFIX");
    expect(sp.get("cause")).toBe("RC_PREFIX");
    expect(sp.get("row")).toBe("r1");
  });
});
