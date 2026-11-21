import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useFixMyAgentBlocked } from "../common";
import { useTestExecutionStore } from "../states";

vi.mock("src/hooks/useKpis", () => ({
  default: () => ({ data: { agent_type: "voice" }, isPending: false }),
}));

describe("Debug failures on a run still in progress", () => {
  it("says it opens once the run finishes", () => {
    useTestExecutionStore.setState({ status: "running" });
    const { result } = renderHook(() => useFixMyAgentBlocked("e1"));
    expect(result.current).toEqual({
      disabled: true,
      reason:
        "The test run is still running. You can debug its failures once it finishes.",
    });
  });
});
