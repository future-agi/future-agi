import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import userEvent from "@testing-library/user-event";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { renderWithRouter, screen, within } from "src/utils/test-utils";
import MCPManagementSection from "./MCPManagementSection";

// Dev's cases for a guardrails draft kept across MCP tabs, run against this
// page, where stored MCP guardrails are edited in a dialog: the Guardrails
// tab's "Edit stored settings" opens guardrails/StoredSettingsDialog.jsx.
// Each case keeps its intent. Where this page differs, dev's assertion sits
// in a "// dev:" comment directly above the assertion that replaces it.
// The dialog is modal: while it is open the tab bar is behind it, so no tab
// is opened with a draft in it, and closing it without saving discards the
// draft. Dev's controls on this page: the "Enable MCP Guardrails" checkbox is
// the "Enabled in stored settings" switch, dev's rate-limit row (Tool Name,
// Max/min, Add) is Tool name, Per minute and Add limit in the "Stored
// per-tool limits" section, where Per minute is a text box and each added
// limit is a row that reads "10 per minute", the blocked tool and allowed
// server inputs take one name per line, "Save Changes" and "Save Guardrails"
// are "Save stored settings", the Playground tab is "Try a tool" and the
// Tools tab moved into Servers. There is no "You have unsaved changes." line:
// an open dialog is what holds a draft.

const mocks = vi.hoisted(() => ({
  gatewayId: "test-gateway",
  config: {},
  mutate: vi.fn(),
  snackbar: vi.fn(),
}));
// The page's other tabs, each a stub that says which tab is open.
const { stubTab } = vi.hoisted(() => ({
  stubTab: (name) => () => ({ default: () => <div>{`${name} content`}</div> }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: mocks.gatewayId, isLoading: false }),
}));
// Stored settings are edited by an Owner or Admin (useGatewayAdmin): these
// cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8, currentOrganizationId: "test-org" }),
}));
vi.mock("../providers/hooks/useGatewayConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  useGatewayConfig: () => ({ data: mocks.config, isLoading: false }),
  useReloadConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("./hooks/useMCPConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  useMCPStatus: () => ({ data: { servers: [] }, isLoading: false }),
  useMCPTools: () => ({ data: [], isLoading: false }),
  useMCPResources: () => ({ data: [], isLoading: false }),
  useMCPPrompts: () => ({ data: [], isLoading: false }),
  useUpdateMCPGuardrails: () => ({ mutate: mocks.mutate, isPending: false }),
}));
vi.mock("./hooks/useMCPConfigPatch", () => ({
  useMCPConfigPatch: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.snackbar }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("./calls/SiemExportDrawer", () => ({ default: () => null }));
vi.mock("./overview/OverviewTab", stubTab("Overview"));
vi.mock("./servers/ServersTab", stubTab("Servers"));
vi.mock("./virtual/VirtualServersTab", stubTab("Virtual servers"));
vi.mock("./calls/ToolCallsTab", stubTab("Tool calls"));
vi.mock("./credentials/CredentialsTab", stubTab("Credentials"));
vi.mock("./clients/MCPClientsTab", stubTab("MCP clients"));
vi.mock("./playground/PlaygroundTab", stubTab("Playground"));
vi.mock("./resources/ResourcesTab", stubTab("Resources"));
vi.mock("./prompts/PromptsTab", stubTab("Prompts"));
vi.mock("./registry/RegistryTab", stubTab("Registry"));
vi.mock("./connect/ConnectTab", stubTab("Connect"));
vi.mock("./trust/TrustTab", stubTab("Tool revisions"));
vi.mock("./approvals/ApprovalsTab", stubTab("Approvals"));

const theme = createTheme({ palette: palette("light"), typography });
const DIALOG = "Stored MCP guardrail settings";
const ENABLED = "Enabled in stored settings";
const LIMITS = "Stored per-tool limits";
const SAVE = "Save stored settings";
// The other tabs this page opens, each with the stub its content shows. Dev's
// Tools tab is Servers here; Tool calls, MCP clients, Resources, Prompts and
// Connect stay in the bar, disabled, with this status.
const OTHER_TABS = [
  ["Overview", "Overview"],
  ["Servers", "Servers"],
  ["Virtual servers", "Virtual servers"],
  ["Credentials", "Credentials"],
  ["Approvals", "Approvals"],
  ["Try a tool", "Playground"],
];

let queryClient;
const MCPRoutes = () => (
  <HelmetProvider>
    <QueryClientProvider client={queryClient}>
      <Routes>
        <Route
          path="/dashboard/gateway/mcp"
          element={<MCPManagementSection />}
        />
        <Route
          path="/dashboard/gateway/mcp/:tab"
          element={<MCPManagementSection />}
        />
      </Routes>
    </QueryClientProvider>
  </HelmetProvider>
);

const renderMCP = () => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderWithRouter(<MCPRoutes />, {
    route: "/dashboard/gateway/mcp/guardrails",
    theme,
  });
};

const switchIn = (dialog, name) =>
  within(dialog).getByRole("checkbox", { name });
const fieldIn = (dialog, label) => within(dialog).getByLabelText(label);
const buttonIn = (dialog, name) => within(dialog).getByRole("button", { name });
// The stored per-tool limits in the dialog, one row per added limit, each as
// it reads.
const rowsIn = (dialog) =>
  within(within(dialog).getByRole("region", { name: LIMITS }))
    .queryAllByRole("listitem")
    .map((row) => row.textContent);

// Opens the dialog from the Guardrails tab.
const openDialog = async (user) => {
  await user.click(
    screen.getByRole("button", { name: "Edit stored settings" }),
  );
  return screen.getByRole("dialog", { name: DIALOG });
};

// While the dialog is open the tab bar is behind it: still on the page, but
// covered and hidden from assistive technology, so no tab can be opened.
const expectTabsBehindDialog = (tabNames) => {
  for (const name of tabNames) {
    expect(screen.queryByRole("tab", { name })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name, hidden: true })).toBeInTheDocument();
  }
};

beforeEach(() => {
  mocks.gatewayId = "test-gateway";
  mocks.config = { mcp: { enabled: true, guardrails: { enabled: false } } };
  mocks.mutate.mockReset();
  mocks.snackbar.mockReset();
});

describe("MCP guardrails drafts across tabs", () => {
  it("preserves enabled guardrails and an added rate limit after a Playground round trip (in the dialog)", async () => {
    // Rewrite ruled: the dialog is modal, so the round trip cannot be made
    // with the draft open. The draft stays while the dialog is open; closing
    // it without saving discards it, so the dialog opens on the saved
    // settings after the round trip.
    const user = userEvent.setup();
    renderMCP();
    const dialog = await openDialog(user);
    await user.click(switchIn(dialog, ENABLED));
    await user.type(fieldIn(dialog, "Tool name"), "test_search");
    await user.type(fieldIn(dialog, "Per minute"), "10");
    await user.click(buttonIn(dialog, "Add limit"));
    expectTabsBehindDialog(["Try a tool"]);
    expect(switchIn(dialog, ENABLED)).toBeChecked();
    expect(rowsIn(dialog)).toEqual(["test_search10 per minute"]);
    // Dev's checks of the added limit, made while the draft is open.
    expect(screen.getByText("test_search")).toBeVisible();
    // dev: expect(screen.getByText("10/min")).toBeVisible();
    expect(screen.getByText("10 per minute")).toBeVisible();

    await user.click(buttonIn(dialog, "Cancel"));
    await user.click(screen.getByRole("tab", { name: "Try a tool" }));
    expect(screen.getByText("Playground content")).toBeVisible();
    // dev: expect(screen.queryByRole("checkbox", { name: "Enable MCP Guardrails" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Edit stored settings" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Guardrails" }));

    const reopened = await openDialog(user);
    // dev: expect(screen.getByRole("checkbox", { name: "Enable MCP Guardrails" })).toBeChecked();
    expect(switchIn(reopened, ENABLED)).not.toBeChecked();
    // dev: expect(screen.getByText("test_search")).toBeVisible();
    // dev: expect(screen.getByText("10/min")).toBeVisible();
    expect(rowsIn(reopened)).toEqual([]);
    // dev: expect(screen.getByText("You have unsaved changes.")).toBeVisible();
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  it("preserves all guardrail fields and unfinished inputs across every other MCP tab (in the dialog)", async () => {
    // Rewrite ruled: the dialog is modal, so no other tab is opened with the
    // draft open. Every field, an unfinished one included, stays while the
    // dialog is open; closing it without saving discards the draft, so after
    // each other tab the dialog opens on the saved settings.
    const user = userEvent.setup();
    renderMCP();
    const dialog = await openDialog(user);
    await user.click(switchIn(dialog, ENABLED));
    await user.click(switchIn(dialog, "Validate tool inputs"));
    await user.click(switchIn(dialog, "Validate tool outputs"));
    await user.type(
      fieldIn(dialog, "Blocked tools"),
      "test_delete{Enter}unfinished_blocked",
    );
    await user.type(fieldIn(dialog, "Allowed servers"), "test-server");
    // A limit typed but not added: the dialog's unfinished input, which holds
    // the save.
    await user.type(fieldIn(dialog, "Tool name"), "unfinished_tool");
    await user.type(fieldIn(dialog, "Per minute"), "25");
    expectTabsBehindDialog(OTHER_TABS.map(([tabName]) => tabName));
    expect(switchIn(dialog, "Validate tool inputs")).toBeChecked();
    expect(switchIn(dialog, "Validate tool outputs")).toBeChecked();
    expect(fieldIn(dialog, "Blocked tools")).toHaveValue(
      "test_delete\nunfinished_blocked",
    );
    expect(fieldIn(dialog, "Allowed servers")).toHaveValue("test-server");
    // Dev's checks of the unfinished limit, made while the draft is open.
    // dev: expect(screen.getByRole("textbox", { name: "Tool Name" })).toHaveValue("unfinished_tool");
    expect(screen.getByRole("textbox", { name: "Tool name" })).toHaveValue(
      "unfinished_tool",
    );
    // dev: expect(screen.getByRole("spinbutton", { name: "Max/min" })).toHaveValue(25);
    expect(screen.getByRole("textbox", { name: "Per minute" })).toHaveValue(
      "25",
    );
    expect(rowsIn(dialog)).toEqual([]);
    expect(buttonIn(dialog, SAVE)).toBeDisabled();
    await user.click(buttonIn(dialog, "Cancel"));

    for (const [tabName, content] of OTHER_TABS) {
      await user.click(screen.getByRole("tab", { name: tabName }));
      // dev: expect(screen.getByText(`${tabName} content`)).toBeVisible();
      expect(screen.getByText(`${content} content`)).toBeVisible();
      // dev: expect(screen.queryByRole("button", { name: "Save Changes" })).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: SAVE }),
      ).not.toBeInTheDocument();
      await user.click(screen.getByRole("tab", { name: "Guardrails" }));
      const reopened = await openDialog(user);
      // dev: expect(screen.getByRole("checkbox", { name: /Validate tool inputs/ })).toBeChecked();
      expect(switchIn(reopened, "Validate tool inputs")).not.toBeChecked();
      // dev: expect(screen.getByRole("checkbox", { name: "Validate tool outputs" })).toBeChecked();
      expect(switchIn(reopened, "Validate tool outputs")).not.toBeChecked();
      // dev: expect(screen.getByText("test_delete")).toBeVisible();
      // dev: expect(screen.getByPlaceholderText("Type a tool name and press Enter...")).toHaveValue("unfinished_blocked");
      expect(fieldIn(reopened, "Blocked tools")).toHaveValue("");
      // dev: expect(screen.getByText("test-server")).toBeVisible();
      expect(fieldIn(reopened, "Allowed servers")).toHaveValue("");
      // dev: expect(screen.getByRole("textbox", { name: "Tool Name" })).toHaveValue("unfinished_tool");
      expect(fieldIn(reopened, "Tool name")).toHaveValue("");
      // dev: expect(screen.getByRole("spinbutton", { name: "Max/min" })).toHaveValue(25);
      expect(fieldIn(reopened, "Per minute")).toHaveValue("");
      expect(rowsIn(reopened)).toEqual([]);
      await user.click(buttonIn(reopened, "Cancel"));
    }
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  it("submits the preserved draft only when the user explicitly saves (in the dialog)", async () => {
    // Steps re-pointed at the dialog: the draft is sent only by its Save.
    // Dev's round trip with the draft open is not reachable (the dialog is
    // modal); the tab bar is behind the dialog instead.
    const user = userEvent.setup();
    mocks.mutate.mockImplementation((variables, { onSuccess }) => {
      // The config read after a save (useUpdateMCPGuardrails refreshes it)
      // returns what was saved.
      mocks.config = {
        mcp: { ...mocks.config.mcp, guardrails: variables.config },
      };
      onSuccess();
    });
    renderMCP();
    const dialog = await openDialog(user);
    await user.click(switchIn(dialog, ENABLED));
    expectTabsBehindDialog(["Try a tool"]);
    expect(mocks.mutate).not.toHaveBeenCalled();
    await user.click(buttonIn(dialog, SAVE));
    // dev: expect(mocks.mutate).toHaveBeenCalledExactlyOnceWith({ gatewayId: "test-gateway", config: { enabled: true, blocked_tools: [], allowed_servers: [], validate_inputs: false, validate_outputs: false, tool_rate_limits: {} } }, expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }));
    expect(mocks.mutate).toHaveBeenCalledExactlyOnceWith(
      {
        gatewayId: "test-gateway",
        config: { enabled: true },
        base: { enabled: false },
      },
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
    // dev: expect(screen.queryByText("You have unsaved changes.")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: DIALOG }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Try a tool" }));
    await user.click(screen.getByRole("tab", { name: "Guardrails" }));
    const reopened = await openDialog(user);
    // dev: expect(screen.getByRole("checkbox", { name: "Enable MCP Guardrails" })).toBeChecked();
    expect(switchIn(reopened, ENABLED)).toBeChecked();
  });

  it("retains the unsaved draft after a failed save and tab switch (in the dialog)", async () => {
    // Steps re-pointed at the dialog: a failed save says so and keeps the
    // dialog open with the draft. Dev's tab switch is not reachable while
    // the dialog is open (it is modal); the tab bar is behind it instead.
    const user = userEvent.setup();
    mocks.mutate.mockImplementation((_, { onError }) => onError());
    renderMCP();
    const dialog = await openDialog(user);
    await user.click(switchIn(dialog, ENABLED));
    await user.click(buttonIn(dialog, SAVE));
    expect(mocks.mutate).toHaveBeenCalledTimes(1);
    expect(mocks.snackbar).toHaveBeenCalledWith(
      "Stored MCP guardrail settings could not be saved.",
      { variant: "error" },
    );
    expectTabsBehindDialog(["Try a tool"]);
    const kept = screen.getByRole("dialog", { name: DIALOG });
    // dev: expect(screen.getByRole("checkbox", { name: "Enable MCP Guardrails" })).toBeChecked();
    expect(switchIn(kept, ENABLED)).toBeChecked();
    // dev: expect(screen.getByText("You have unsaved changes.")).toBeVisible();
    expect(kept).toBeInTheDocument();
  });

  it("does not carry a draft into another gateway with the same saved settings (in the dialog)", async () => {
    // Steps re-pointed at the dialog: the gateway changes while the dialog is
    // open with a draft. Dev's Playground step is not reachable while the
    // dialog is open (it is modal); the tab bar is behind it instead.
    const user = userEvent.setup();
    const view = renderMCP();
    const dialog = await openDialog(user);
    await user.click(switchIn(dialog, ENABLED));
    await user.type(fieldIn(dialog, "Tool name"), "first_gateway_only");
    await user.type(fieldIn(dialog, "Blocked tools"), "first_gateway_only");
    expectTabsBehindDialog(["Try a tool"]);
    mocks.gatewayId = "another-test-gateway";
    view.rerender(<MCPRoutes />);
    expect(
      screen.queryByRole("dialog", { name: DIALOG }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Guardrails" }));
    const reopened = await openDialog(user);
    // dev: expect(screen.getByRole("checkbox", { name: "Enable MCP Guardrails" })).not.toBeChecked();
    expect(switchIn(reopened, ENABLED)).not.toBeChecked();
    // dev: expect(screen.getByRole("textbox", { name: "Tool Name" })).toHaveValue("");
    expect(screen.getByRole("textbox", { name: "Tool name" })).toHaveValue("");
    expect(fieldIn(reopened, "Blocked tools")).toHaveValue("");
    // dev: expect(screen.queryByText("You have unsaved changes.")).not.toBeInTheDocument();
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  it("shows same-length list changes from a refetch made while the tab was hidden (in the dialog)", async () => {
    // Steps re-pointed at the dialog: the stored summary shows the refetched
    // lists, and the save is made from the dialog, which opens on them.
    const user = userEvent.setup();
    mocks.config = {
      mcp: {
        enabled: true,
        guardrails: {
          enabled: true,
          blocked_tools: ["old_tool"],
          allowed_servers: ["old-server"],
        },
      },
    };
    const view = renderMCP();
    expect(screen.getByText("old_tool")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Try a tool" }));
    mocks.config = {
      mcp: {
        enabled: true,
        guardrails: {
          enabled: true,
          blocked_tools: ["new_tool"],
          allowed_servers: ["new-server"],
        },
      },
    };
    view.rerender(<MCPRoutes />);
    await user.click(screen.getByRole("tab", { name: "Guardrails" }));
    expect(screen.getByText("new_tool")).toBeVisible();
    expect(screen.getByText("new-server")).toBeVisible();
    expect(screen.queryByText("old_tool")).not.toBeInTheDocument();
    expect(screen.queryByText("old-server")).not.toBeInTheDocument();
    const dialog = await openDialog(user);
    await user.click(buttonIn(dialog, SAVE));
    // dev: expect(mocks.mutate).toHaveBeenCalledExactlyOnceWith({ gatewayId: "test-gateway", config: { enabled: true, blocked_tools: ["new_tool"], allowed_servers: ["new-server"], validate_inputs: false, validate_outputs: false, tool_rate_limits: {} } }, expect.any(Object));
    expect(mocks.mutate).toHaveBeenCalledExactlyOnceWith(
      {
        gatewayId: "test-gateway",
        config: {
          enabled: true,
          blocked_tools: ["new_tool"],
          allowed_servers: ["new-server"],
        },
        base: {
          enabled: true,
          blocked_tools: ["new_tool"],
          allowed_servers: ["new-server"],
        },
      },
      expect.any(Object),
    );
  });

  it("keeps the draft when a refetch returns the same guardrail values (in the dialog)", async () => {
    // Steps re-pointed at the dialog: the refetch lands while the dialog is
    // open with the draft. Dev's Playground step is not reachable while the
    // dialog is open (it is modal); the tab bar is behind it instead.
    const user = userEvent.setup();
    mocks.config = {
      mcp: {
        enabled: true,
        guardrails: { enabled: true, blocked_tools: ["kept_tool"] },
      },
    };
    const view = renderMCP();
    const dialog = await openDialog(user);
    await user.click(switchIn(dialog, "Validate tool outputs"));
    expectTabsBehindDialog(["Try a tool"]);
    mocks.config = {
      mcp: {
        enabled: true,
        guardrails: { enabled: true, blocked_tools: ["kept_tool"] },
      },
    };
    view.rerender(<MCPRoutes />);
    const kept = screen.getByRole("dialog", { name: DIALOG });
    // dev: expect(screen.getByRole("checkbox", { name: "Validate tool outputs" })).toBeChecked();
    expect(switchIn(kept, "Validate tool outputs")).toBeChecked();
    // dev: expect(screen.getByText("You have unsaved changes.")).toBeVisible();
    expect(kept).toBeInTheDocument();
    expect(mocks.mutate).not.toHaveBeenCalled();
  });
});
