import React from "react";
import { expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderMcp, demoToolCall } from "../testing/renderMcp";
import ToolCallDrawer from "./ToolCallDrawer";

vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { features: { tool_call_explorer: true } } }),
}));

vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: ({ traceId, readOnly }) => (
    <div role="status">{`${traceId}: ${readOnly ? "read-only" : "editable"}`}</div>
  ),
}));
it("opens the recorded trace using the existing read-only trace viewer", async () => {
  renderMcp(
    <ToolCallDrawer
      gatewayId="gw-1"
      call={{
        ...demoToolCall(),
        content_mode: "none",
        agent_trace_id: "trace-synthetic",
      }}
      onClose={() => {}}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open trace" }));
  expect(
    await screen.findByText("trace-synthetic: read-only"),
  ).toBeInTheDocument();
});
