import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import GuardrailsTab from "./GuardrailsTab";

const mockMutate = vi.fn();

vi.mock("../hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { servers: [] } }),
  useUpdateMCPGuardrails: () => ({ mutate: mockMutate, isPending: false }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
// Turning stored MCP guardrails off writes the org's gateway config: Owner
// or Admin only (useGatewayAdmin). These cases act as a Member unless they
// set another level.
const { org } = vi.hoisted(() => ({ org: { level: 3 } }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () =>
    query.current ?? { data: config.current, isLoading: false },
}));

const { config, query } = vi.hoisted(() => ({
  query: { current: null },
  config: {
    current: {
      mcp: {
        guardrails: {
          enabled: true,
          blocked_tools: ["shell"],
          allowed_servers: ["github"],
          validate_inputs: true,
          validate_outputs: false,
          tool_rate_limits: { github__search: 10 },
          // A key an older build of this tab wrote.
          custom_patterns: ["(?i)password"],
        },
      },
    },
  },
}));

const CONTROLS =
  "input:not([type=hidden]):not([aria-hidden=true]), textarea, [role=combobox], [role=switch], [role=checkbox], [role=option]";

// The summary reports stored settings, not live enforcement. Members see
// disabled actions; admins can edit stored settings in a separate dialog.
describe("MCP > Guardrails", () => {
  it("shows a member the stored summary with Edit disabled and no save form", () => {
    render(<GuardrailsTab gatewayId="default" />);
    const stored = screen.getByTestId("mcp-guardrails-recorded-only");
    expect(
      within(stored).getByTestId("enforcement-mcp.guardrails"),
    ).toHaveTextContent("Not enforced on this gateway");
    for (const text of ["shell", "github", "github__search: 10 per minute"]) {
      expect(stored).toHaveTextContent(text);
    }
    expect(document.body.querySelectorAll(CONTROLS)).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /Save/ })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Edit stored settings" }),
    ).toBeDisabled();
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("shows legacy custom patterns as stored, without claiming enforcement", () => {
    render(<GuardrailsTab gatewayId="default" />);
    const stored = screen.getByTestId("mcp-guardrails-recorded-only");
    expect(stored).toHaveTextContent("(?i)password");
    expect(stored).toHaveTextContent("Custom injection patterns (stored only)");
    expect(
      within(stored).getByTestId("enforcement-mcp.guardrails"),
    ).toHaveTextContent("Not enforced on this gateway");
    expect(document.body.querySelectorAll(CONTROLS)).toHaveLength(0);
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it.each(["stored", "empty"])(
    "850: labels %s settings truthfully without a diagnostic explanation",
    (state) => {
      if (state === "empty")
        query.current = { data: { mcp: {} }, isLoading: false };
      render(<GuardrailsTab gatewayId="default" />);
      expect(
        screen.getByText(
          "Saved MCP guardrail settings are stored for your organization. They are not active on this gateway.",
        ),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /virtual server/i }),
      ).toBeNull();
      expect(screen.queryByText(/virtual server/i)).toBeNull();
      expect(
        screen.getByTestId("enforcement-mcp.guardrails"),
      ).toHaveTextContent(
        "Saved MCP guardrail settings are stored with your organization's configuration.",
      );
      expect(document.body).not.toHaveTextContent(
        /not checked|not read by the gateway|applies only its own MCP tool guard/,
      );
    },
  );

  afterEach(() => {
    query.current = null;
    org.level = 3;
    mockMutate.mockReset();
  });

  // A failed config fetch proves nothing about what is stored, so it reads
  // as an error with a retry, never as "No displayable MCP guardrail settings."
  it("shows the shared error state with a retry when the config does not load", async () => {
    const refetch = vi.fn();
    query.current = {
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    };
    render(<GuardrailsTab gatewayId="default" />);
    expect(
      screen.queryByText("No displayable MCP guardrail settings."),
    ).toBeNull();
    expect(screen.queryByTestId("mcp-guardrails-recorded-only")).toBeNull();
    expect(
      screen.getByText("Organization MCP guardrails could not be loaded"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  // Stored guardrails that are on can be turned off by an Owner or Admin,
  // confirmed: they are stored again with enabled off and every other value
  // as it was. The separate stored-settings editor can also add or edit them.
  it("lets an Admin turn stored guardrails off, once confirmed, keeping every other stored value", async () => {
    org.level = 8;
    const user = userEvent.setup();
    const stored = structuredClone(config.current.mcp.guardrails);
    render(<GuardrailsTab gatewayId="default" />);
    await user.click(screen.getByRole("button", { name: "Turn off" }));
    expect(
      screen.getByText(/Turn the stored MCP guardrails off\?/),
    ).toBeInTheDocument();
    expect(mockMutate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Keep on" }));
    expect(mockMutate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Turn off" }));
    await user.click(screen.getByRole("button", { name: "Turn off" }));
    expect(mockMutate).toHaveBeenCalledTimes(1);
    const [variables] = mockMutate.mock.calls[0];
    expect(variables.gatewayId).toBe("default");
    expect(JSON.stringify(variables.config)).toBe(
      JSON.stringify({ ...stored, enabled: false }),
    );
    // The control sits outside the read-only block.
    expect(
      within(screen.getByTestId("mcp-guardrails-recorded-only")).queryByRole(
        "button",
        { name: "Turn off" },
      ),
    ).toBeNull();
  });

  it("shows a Member the stored guardrails with Turn off disabled", () => {
    render(<GuardrailsTab gatewayId="default" />);
    expect(screen.getByRole("button", { name: "Turn off" })).toBeDisabled();
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("850: keeps editing stored settings available while they are off", () => {
    org.level = 8;
    const previous = config.current;
    config.current = {
      mcp: { guardrails: { enabled: false, blocked_tools: ["shell"] } },
    };
    render(<GuardrailsTab gatewayId="default" />);
    expect(
      screen.getByTestId("mcp-guardrails-recorded-only"),
    ).toHaveTextContent("shell");
    expect(screen.queryByRole("button", { name: "Turn off" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Turn on/ })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Edit stored settings" }),
    ).toBeEnabled();
    config.current = previous;
  });

  it("850: creates stored settings from the empty state without claiming activation", async () => {
    org.level = 8;
    config.current = { mcp: {} };
    const user = userEvent.setup();
    render(<GuardrailsTab gatewayId="default" />);
    expect(
      screen.getByText("No displayable MCP guardrail settings."),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Add stored settings" }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Stored MCP guardrail settings",
    });
    await user.type(within(dialog).getByLabelText("Blocked tools"), "shell");
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "Enabled in stored settings",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Save stored settings" }),
    );
    expect(mockMutate).toHaveBeenCalledWith(
      {
        gatewayId: "default",
        config: expect.objectContaining({
          enabled: true,
          blocked_tools: ["shell"],
        }),
      },
      expect.anything(),
    );
  });

  it("850: edits stored settings and preserves fields the editor does not own", async () => {
    org.level = 8;
    const stored = {
      enabled: false,
      blocked_tools: ["shell"],
      custom_patterns: ["legacy"],
      extension: { keep: true },
    };
    config.current = { mcp: { guardrails: stored } };
    const user = userEvent.setup();
    render(<GuardrailsTab gatewayId="default" />);
    await user.click(
      screen.getByRole("button", { name: "Edit stored settings" }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Stored MCP guardrail settings",
    });
    await user.click(
      within(dialog).getByRole("checkbox", {
        name: "Enabled in stored settings",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Save stored settings" }),
    );
    expect(mockMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      config: { ...stored, enabled: true },
    });
    expect(stored.enabled).toBe(false);
  });

  it("850: retains disabled stored-settings actions for a member", () => {
    config.current = { mcp: {} };
    render(<GuardrailsTab gatewayId="default" />);
    expect(
      screen.getByRole("button", { name: "Add stored settings" }),
    ).toBeDisabled();
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("says nothing is stored when the organization has no MCP guardrails", () => {
    config.current = { mcp: {} };
    render(<GuardrailsTab gatewayId="default" />);
    expect(
      screen.getByText("No displayable MCP guardrail settings."),
    ).toBeInTheDocument();
    expect(document.body.querySelectorAll(CONTROLS)).toHaveLength(0);
  });
});
