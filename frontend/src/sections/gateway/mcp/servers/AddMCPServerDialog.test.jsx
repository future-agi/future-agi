import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import AddMCPServerDialog from "./AddMCPServerDialog";

const mockMutate = vi.fn();
let mockMutationState = {};
const { snackbars } = vi.hoisted(() => ({ snackbars: [] }));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

vi.mock("../hooks/useMCPConfig", () => ({
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
    snackbars.length = 0;
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

  it("edit dialog never renders a stored token", () => {
    // Given a server config that holds a credential value, the dialog
    // never copies that value into the form.
    render(
      <AddMCPServerDialog
        open
        onClose={vi.fn()}
        gatewayId="default"
        editServer={{
          serverId: "remote",
          config: {
            transport: "http",
            url: "http://old:1",
            auth: { type: "bearer", token: "stored-bearer-value-0001" },
          },
        }}
      />,
    );

    expect(
      screen.getByLabelText("Bearer Token", { selector: "input" }),
    ).toHaveValue("");
    expect(screen.getByText(/Stored credential: set/)).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("stored-bearer-value-0001");
  });

  it("says not set when the stored server has no credential", () => {
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

    expect(screen.getByText(/Stored credential: not set/)).toBeInTheDocument();
  });

  it("asks for the token before saving a bearer server", async () => {
    const user = userEvent.setup();
    render(<AddMCPServerDialog open onClose={vi.fn()} gatewayId="default" />);

    await user.type(screen.getByLabelText(/Server ID/), "github");
    await user.type(screen.getByLabelText(/URL/), "http://mcp:8080");
    await user.click(screen.getByLabelText(/Auth Type/));
    await user.click(screen.getByRole("option", { name: "Bearer Token" }));
    await user.click(screen.getByRole("button", { name: "Add Server" }));

    expect(mockMutate).not.toHaveBeenCalled();
    expect(snackbars.map(({ message }) => message)).toEqual([
      "Enter the bearer token to save this server",
    ]);

    await user.type(
      screen.getByLabelText("Bearer Token", { selector: "input" }),
      "typed-value-0004",
    );
    await user.click(screen.getByRole("button", { name: "Add Server" }));

    expect(mockMutate.mock.calls[0][0].config).toEqual({
      transport: "http",
      url: "http://mcp:8080",
      auth: { type: "bearer", token: "typed-value-0004" },
    });
  });

  it("has no tools cache TTL field", () => {
    render(<AddMCPServerDialog open onClose={vi.fn()} gatewayId="default" />);

    expect(screen.queryByLabelText(/Cache TTL/)).toBeNull();
  });

  it("does not send a stored tools cache TTL back", async () => {
    const user = userEvent.setup();
    render(
      <AddMCPServerDialog
        open
        onClose={vi.fn()}
        gatewayId="default"
        editServer={{
          serverId: "remote",
          config: {
            transport: "http",
            url: "http://old:1",
            tools_cache_ttl: "5m",
          },
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
