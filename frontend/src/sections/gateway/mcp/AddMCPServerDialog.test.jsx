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

// What every org-config read returns for a stored MCP secret: the
// mask, never the value. The Edit dialog shows it as dots with Replace and
// Remove; sending the server back untouched sends the mask, which the backend
// resolves to the stored secret (keep), Replace sends a blank (keep, unless a
// new value is typed) and Remove sends null (delete).
const MASK = "__encrypted__0123456789abcdef";

describe("AddMCPServerDialog editing a server with a stored secret", () => {
  beforeEach(() => {
    mockMutate.mockClear();
    mockMutationState = {};
  });

  const bearer = {
    serverId: "gh",
    config: {
      transport: "http",
      url: "https://mcp.example/gh/mcp",
      auth: { type: "bearer", token: MASK },
    },
  };
  const apiKey = {
    serverId: "kv",
    config: {
      transport: "http",
      url: "https://mcp.example/kv/mcp",
      auth: { type: "api_key", header: "X-API-Key", key: MASK },
    },
  };

  function renderEdit(editServer) {
    return render(
      <AddMCPServerDialog
        open
        onClose={vi.fn()}
        gatewayId="default"
        editServer={editServer}
      />,
    );
  }

  function expectNoMaskInAnyInput() {
    for (const input of document.querySelectorAll("input, textarea")) {
      expect(input.value).not.toContain("__encrypted__");
    }
  }

  it.each([
    ["bearer token", bearer, "Bearer Token"],
    ["API key", apiKey, "API Key"],
  ])(
    "shows the stored %s as dots with Replace and Remove",
    (_kind, server, label) => {
      renderEdit(server);

      // The auth-type select shows the same words, so take the input.
      const input = screen.getByLabelText(label, { selector: "input" });
      expect(input).toHaveAttribute("readonly");
      expect(input.value).toMatch(/•+/);
      expectNoMaskInAnyInput();
      expect(
        screen.getByRole("button", { name: /replace/i }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /remove/i }),
      ).toBeInTheDocument();
    },
  );

  it.each([
    ["token", bearer],
    ["key", apiKey],
  ])(
    "an untouched %s is sent back as the mask, so it is kept",
    async (field, server) => {
      const user = userEvent.setup();
      renderEdit(server);

      await user.click(screen.getByRole("button", { name: "Update Server" }));

      expect(mockMutate.mock.calls[0][0].config.auth[field]).toBe(MASK);
    },
  );

  it("Replace with nothing typed sends a blank, which keeps the stored token", async () => {
    const user = userEvent.setup();
    renderEdit(bearer);

    await user.click(screen.getByRole("button", { name: /replace/i }));
    const input = screen.getByLabelText("Bearer Token", { selector: "input" });
    expect(input).not.toHaveAttribute("readonly");
    expect(input).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Update Server" }));

    expect(mockMutate.mock.calls[0][0].config.auth.token).toBe("");
  });

  it("Replace then a typed key sends the new key", async () => {
    const user = userEvent.setup();
    renderEdit(apiKey);

    await user.click(screen.getByRole("button", { name: /replace/i }));
    await user.type(
      screen.getByLabelText("API Key", { selector: "input" }),
      "new-key",
    );
    await user.click(screen.getByRole("button", { name: "Update Server" }));

    expect(mockMutate.mock.calls[0][0].config.auth).toEqual({
      type: "api_key",
      header: "X-API-Key",
      key: "new-key",
    });
  });

  it("Remove sends null, which deletes the stored token", async () => {
    const user = userEvent.setup();
    renderEdit(bearer);

    await user.click(screen.getByRole("button", { name: /remove/i }));
    await user.click(screen.getByRole("button", { name: "Update Server" }));

    expect(mockMutate.mock.calls[0][0].config.auth).toEqual({
      type: "bearer",
      token: null,
    });
  });
});
