import { describe, it, expect } from "vitest";
import { getObservePaneTabLabel } from "../observePaneTabLabels";

describe("getObservePaneTabLabel", () => {
  it("uses Graph labels when the observe view is graph mode", () => {
    expect(getObservePaneTabLabel("graph", "primary")).toBe("Primary Graph");
    expect(getObservePaneTabLabel("graph", "compare")).toBe("Comparison Graph");
  });

  it("uses Data labels for table view so tabs match TraceGrid content", () => {
    expect(getObservePaneTabLabel("table", "primary")).toBe("Primary Data");
    expect(getObservePaneTabLabel("table", "compare")).toBe("Comparison Data");
  });

  it("uses Data labels for agent views (not graph charts)", () => {
    expect(getObservePaneTabLabel("agentGraph", "primary")).toBe("Primary Data");
    expect(getObservePaneTabLabel("agentPath", "compare")).toBe(
      "Comparison Data",
    );
  });
});
