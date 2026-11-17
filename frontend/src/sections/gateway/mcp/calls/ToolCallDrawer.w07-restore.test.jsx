import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderMcp, demoToolCall } from "../testing/renderMcp";
import ToolCallDrawer from "./ToolCallDrawer";

// The drawer's Virtual server row follows the calls list's rule: a call's
// recorded virtual server stays readable after the deployment's switch is
// turned off, and an empty one keeps "None (the org's default view)".
const { status } = vi.hoisted(() => ({ status: { current: {} } }));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => status.current,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));

const NOT_AVAILABLE = "Not available in this deployment";

function virtualServerRow(call) {
  renderMcp(<ToolCallDrawer gatewayId="gw-1" call={call} onClose={() => {}} />);
  const term = [...document.querySelectorAll("dt")].find(
    (dt) => dt.textContent === "Virtual server",
  );
  return term?.nextElementSibling?.textContent;
}

beforeEach(() => {
  status.current = {
    data: {
      enabled: true,
      features: { tool_call_explorer: true, virtual_servers: false },
    },
  };
});

describe("tool-call drawer with the virtual servers switch off", () => {
  it("shows the recorded virtual server, as the list does", () => {
    expect(
      virtualServerRow(demoToolCall({ virtual_server_id: "support-desk" })),
    ).toBe("support-desk");
    expect(screen.queryByText(NOT_AVAILABLE)).toBeNull();
  });

  it("keeps None (the org's default view) when none was recorded", () => {
    expect(virtualServerRow(demoToolCall({ virtual_server_id: "" }))).toBe(
      "None (the org's default view)",
    );
    expect(screen.queryByText(NOT_AVAILABLE)).toBeNull();
  });

  it("shows the recorded virtual server with the switch on", () => {
    status.current = {
      data: {
        enabled: true,
        features: { tool_call_explorer: true, virtual_servers: true },
      },
    };
    expect(
      virtualServerRow(demoToolCall({ virtual_server_id: "support-desk" })),
    ).toBe("support-desk");
  });
});
