import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import AddMCPServerDialog from "./AddMCPServerDialog";

const mockMutate = vi.fn();
let mockMutationState = {};

vi.mock("./hooks/useMCPConfig", () => ({
  useUpdateMCPServer: () => ({
    mutate: mockMutate,
    isPending: false,
    ...mockMutationState,
  }),
}));

describe("AddMCPServerDialog", () => {
  beforeEach(() => {
    mockMutate.mockClear();
    mockMutationState = {};
  });

  it("shows the reason when the save is rejected", () => {
    mockMutationState = {
      isError: true,
      error: {
        message:
          "The gateway cannot accept this config. mcp.servers.github.command: Extra inputs are not permitted",
      },
    };
    render(<AddMCPServerDialog open onClose={vi.fn()} gatewayId="default" />);

    expect(
      screen.getByText(/mcp\.servers\.github\.command: Extra inputs/),
    ).toBeInTheDocument();
  });

  it("saves an HTTP server with only the fields the gateway config has", async () => {
    const user = userEvent.setup();
    render(<AddMCPServerDialog open onClose={vi.fn()} gatewayId="default" />);

    await user.type(screen.getByLabelText(/Server ID/), "github");
    await user.type(screen.getByLabelText(/URL/), "http://mcp:8080");
    await user.click(screen.getByRole("button", { name: "Add Server" }));

    expect(mockMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      serverId: "github",
      config: { transport: "http", url: "http://mcp:8080" },
    });
  });

  it("does not offer a stdio transport", () => {
    render(<AddMCPServerDialog open onClose={vi.fn()} gatewayId="default" />);

    expect(screen.queryByLabelText(/Transport/)).toBeNull();
    expect(screen.queryByLabelText(/Command/)).toBeNull();
  });

  it("opens a stored server without carrying a command forward", async () => {
    const user = userEvent.setup();
    render(
      <AddMCPServerDialog
        open
        onClose={vi.fn()}
        gatewayId="default"
        editServer={{
          serverId: "remote",
          config: { transport: "http", url: "http://old:1" },
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Update Server" }));

    expect(mockMutate.mock.calls[0][0].config).toEqual({
      transport: "http",
      url: "http://old:1",
    });
  });
});
