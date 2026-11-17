import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import AddMCPServerDialog from "./AddMCPServerDialog";
import { renderMcp } from "../testing/renderMcp";

const calls = vi.hoisted(() => ({ save: vi.fn(), probe: vi.fn() }));
vi.mock("../hooks/useMCPConfig", () => ({
  useUpdateMCPServer: () => ({ mutate: calls.save }),
}));
vi.mock("../hooks/useMCPProbe", () => ({
  useMCPProbe: () => ({ mutateAsync: calls.probe, reset: vi.fn() }),
}));
vi.mock("notistack", async (original) => ({
  ...(await original()),
  enqueueSnackbar: vi.fn(),
}));
beforeEach(() => {
  calls.save.mockReset();
  calls.probe.mockReset();
});

describe("RL-1009 saved server writes versus live connection tests", () => {
  it("keeps Save independent of a disabled probe and guards direct probe invocation", async () => {
    renderMcp(
      <AddMCPServerDialog
        open
        runtimeOff
        gatewayId="gw"
        onClose={vi.fn()}
        editServer={{
          serverId: "alpha",
          config: {
            url: "https://alpha.example/mcp",
            transport: "http",
            enabled: true,
          },
        }}
      />,
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    expect(screen.getByLabelText(/URL/)).toBeEnabled();
    const test = screen.getByRole("button", { name: "Test connection" });
    expect(test).toBeDisabled();
    const key = Object.keys(test).find((name) =>
      name.startsWith("__reactProps$"),
    );
    await act(async () => {
      await test[key].onClick();
    });
    expect(calls.probe).not.toHaveBeenCalled();
    expect(
      screen.getByText(/Live MCP operations are unavailable/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(calls.save).toHaveBeenCalledWith(
      {
        gatewayId: "gw",
        serverId: "alpha",
        config: {
          url: "https://alpha.example/mcp",
          transport: "http",
          enabled: true,
        },
      },
      expect.any(Object),
    );
  });
});
