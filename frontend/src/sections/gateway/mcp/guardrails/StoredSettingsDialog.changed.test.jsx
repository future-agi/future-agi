// A stored-settings save Django refuses because another admin changed the
// stored MCP guardrails after the dialog opened (409 mcp_guardrails_changed).
// The save names the settings it was made on (base). On that refusal the
// dialog keeps the person's draft, says so in one sentence and offers Reload,
// which reads the stored settings into the summary and rebases the draft on
// them: the fields the person changed keep their edits, every other field
// shows the re-read value, a field changed and changed back included. Closed
// after that refusal, the tab reads the stored settings again. Every other
// refusal keeps today's toast.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import { handleError } from "src/utils/queryErrorHandler";
import GuardrailsTab from "./GuardrailsTab";

// The dialog's sentence, the Turn off confirmation's (which has no Reload,
// and reads the settings again by itself), and Django's message, which the
// console says in those words instead.
const CHANGED =
  "Someone changed these guardrail settings after you opened them. Reload to see the saved version; your unsaved edits here are kept until you close.";
const TURN_OFF_CHANGED =
  "Someone changed these guardrail settings after this page loaded them, so nothing was turned off. Check the saved settings before you try again.";
const REFUSED =
  "These MCP guardrail settings changed after you opened them, so nothing was saved. Reload them and save again.";
const SAVED = {
  data: {
    status: true,
    result: { action: "updated", version: 3, gateway_synced: true },
  },
};
const SAVE_URL = "/agentcc/gateways/gw-1/update-mcp-guardrails/";
const DIALOG = "Stored MCP guardrail settings";

const { post, get, snackbars } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  snackbars: [],
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

// Stored settings are edited by an Owner or Admin (useGatewayAdmin); these
// cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

vi.mock("src/utils/axios", () => {
  const p = (name) => (id) => `/agentcc/gateways/${id}/${name}/`;
  return {
    default: { post, get },
    endpoints: {
      gateway: {
        config: p("config"),
        updateMcpGuardrails: p("update-mcp-guardrails"),
        mcpStatus: p("mcp-status"),
      },
    },
  };
});

// What the axios interceptor (src/utils/axios.js) rejects a refused request
// with: Django's error body and the status.
const refusal = (statusCode, code, message) => ({
  status: false,
  code,
  message,
  detail: message,
  error: message,
  result: message,
  statusCode,
});

// Stored when the dialog opens, and another admin's save after that.
const OPENED = {
  enabled: false,
  blocked_tools: ["shell"],
  extension: { keep: true },
};
const THEIRS = { enabled: false, blocked_tools: ["browser"] };

let stored;

function renderTab() {
  // The app's wiring (src/app.jsx): every failed mutation also reaches the
  // app-wide handleError, which toasts an error that carries a result.
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <GuardrailsTab gatewayId="gw-1" />
    </QueryClientProvider>,
  );
}

async function openEditor(user) {
  await user.click(
    await screen.findByRole("button", { name: "Edit stored settings" }),
  );
  return screen.getByRole("dialog", { name: DIALOG });
}

const summary = () => screen.getByTestId("mcp-guardrails-recorded-only");

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  snackbars.length = 0;
  stored = OPENED;
  get.mockImplementation(async (url) => {
    if (url === "/agentcc/gateways/gw-1/config/")
      return {
        data: { status: true, result: { mcp: { guardrails: stored } } },
      };
    return { data: { status: true, result: {} } };
  });
});

describe("MCP guardrails saved on settings that changed since they were opened", () => {
  it("the dialog keeps the draft, says so once and offers Reload; saved again, the draft is made on what Reload read", async () => {
    const user = userEvent.setup();
    renderTab();
    const dialog = await openEditor(user);
    await user.click(
      within(dialog).getByRole("checkbox", { name: "Validate tool inputs" }),
    );
    await user.type(
      within(dialog).getByLabelText("Blocked tools"),
      "{Enter}write",
    );
    const draft = {
      ...OPENED,
      validate_inputs: true,
      blocked_tools: ["shell", "write"],
    };

    // Another admin saves meanwhile; this save is made on what was opened.
    stored = THEIRS;
    post.mockRejectedValueOnce(refusal(409, "mcp_guardrails_changed", REFUSED));
    await user.click(
      within(dialog).getByRole("button", { name: "Save stored settings" }),
    );

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]).toEqual([
      SAVE_URL,
      { config: draft, base: OPENED },
    ]);
    expect(await within(dialog).findByText(CHANGED)).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Reload" }),
    ).toBeEnabled();
    expect(screen.getByRole("dialog", { name: DIALOG })).toBe(dialog);
    expect(
      within(dialog).getByRole("checkbox", { name: "Validate tool inputs" }),
    ).toBeChecked();
    expect(within(dialog).getByLabelText("Blocked tools")).toHaveValue(
      "shell\nwrite",
    );
    // One sentence, in the dialog: no toast, the app-wide one included.
    expect(snackbars).toEqual([]);
    expect(summary()).toHaveTextContent("shell");

    // Reload reads the stored settings into the summary; the draft stays.
    await user.click(within(dialog).getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(summary()).toHaveTextContent("browser"));
    await waitFor(() => expect(within(dialog).queryByText(CHANGED)).toBeNull());
    expect(within(dialog).getByLabelText("Blocked tools")).toHaveValue(
      "shell\nwrite",
    );
    expect(
      within(dialog).getByRole("checkbox", { name: "Validate tool inputs" }),
    ).toBeChecked();

    post.mockResolvedValueOnce(SAVED);
    await user.click(
      within(dialog).getByRole("button", { name: "Save stored settings" }),
    );
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    // Made on what Reload read: the two fields this person changed keep
    // the edits (blocked_tools, which the other admin also changed, is this
    // person's visible choice); every other field is the other admin's, so
    // `extension`, which their version no longer has, is not written back.
    expect(post.mock.calls[1]).toEqual([
      SAVE_URL,
      {
        config: {
          ...THEIRS,
          validate_inputs: true,
          blocked_tools: ["shell", "write"],
        },
        base: THEIRS,
      },
    ]);
    expect(post.mock.calls[1][1].config).not.toHaveProperty("extension");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: DIALOG })).toBeNull(),
    );
    expect(snackbars).toEqual([
      {
        message: "MCP guardrail settings saved",
        options: { variant: "success" },
      },
    ]);
  });

  it("after Reload, every field this person did not change shows the other admin's version, and the save keeps it", async () => {
    const user = userEvent.setup();
    renderTab();
    const dialog = await openEditor(user);
    await user.click(
      within(dialog).getByRole("checkbox", { name: "Validate tool inputs" }),
    );

    // The other admin changes fields this person never touched.
    const theirs = {
      enabled: true,
      blocked_tools: ["browser"],
      allowed_servers: ["crm"],
      tool_rate_limits: { crm__search: 5 },
    };
    stored = theirs;
    post.mockRejectedValueOnce(refusal(409, "mcp_guardrails_changed", REFUSED));
    await user.click(
      within(dialog).getByRole("button", { name: "Save stored settings" }),
    );
    await user.click(
      await within(dialog).findByRole("button", { name: "Reload" }),
    );

    await waitFor(() =>
      expect(within(dialog).getByLabelText("Blocked tools")).toHaveValue(
        "browser",
      ),
    );
    expect(within(dialog).getByLabelText("Allowed servers")).toHaveValue("crm");
    expect(
      within(dialog)
        .getAllByRole("listitem")
        .map((row) => row.textContent),
    ).toEqual(["crm__search5 per minute"]);
    expect(
      within(dialog).getByRole("checkbox", {
        name: "Enabled in stored settings",
      }),
    ).toBeChecked();
    // The one field this person changed keeps the edit.
    expect(
      within(dialog).getByRole("checkbox", { name: "Validate tool inputs" }),
    ).toBeChecked();

    post.mockResolvedValueOnce(SAVED);
    await user.click(
      within(dialog).getByRole("button", { name: "Save stored settings" }),
    );
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1]).toEqual([
      SAVE_URL,
      { config: { ...theirs, validate_inputs: true }, base: theirs },
    ]);
    expect(post.mock.calls[1][1].config).not.toHaveProperty("extension");
  });

  it("closed after that refusal, the tab reads the stored settings again, and the next Edit is made on them", async () => {
    const user = userEvent.setup();
    renderTab();
    const dialog = await openEditor(user);

    stored = THEIRS;
    post.mockRejectedValueOnce(refusal(409, "mcp_guardrails_changed", REFUSED));
    await user.click(
      within(dialog).getByRole("button", { name: "Save stored settings" }),
    );
    expect(await within(dialog).findByText(CHANGED)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: DIALOG })).toBeNull(),
    );
    await waitFor(() => expect(summary()).toHaveTextContent("browser"));
    const reopened = await openEditor(user);
    expect(within(reopened).queryByText(CHANGED)).toBeNull();
    expect(within(reopened).getByLabelText("Blocked tools")).toHaveValue(
      "browser",
    );
    await user.click(
      within(reopened).getByRole("checkbox", { name: "Validate tool inputs" }),
    );

    post.mockResolvedValueOnce(SAVED);
    await user.click(
      within(reopened).getByRole("button", { name: "Save stored settings" }),
    );
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1]).toEqual([
      SAVE_URL,
      { config: { ...THEIRS, validate_inputs: true }, base: THEIRS },
    ]);
    expect(snackbars).toEqual([
      {
        message: "MCP guardrail settings saved",
        options: { variant: "success" },
      },
    ]);
  });

  it("any other refused save keeps today's toast, and offers no Reload", async () => {
    const user = userEvent.setup();
    renderTab();
    const dialog = await openEditor(user);
    post.mockRejectedValueOnce(
      refusal(400, "invalid", "The gateway cannot accept this config."),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Save stored settings" }),
    );

    await waitFor(() =>
      expect(snackbars).toContainEqual({
        message: "The gateway cannot accept this config.",
        options: { variant: "error" },
      }),
    );
    expect(screen.getByRole("dialog", { name: DIALOG })).toBe(dialog);
    expect(screen.queryByText(CHANGED)).toBeNull();
    expect(within(dialog).queryByRole("button", { name: "Reload" })).toBeNull();
  });

  it("a Turn off refused for that reason says so once and reads the settings again", async () => {
    const opened = { enabled: true, blocked_tools: ["shell"] };
    stored = opened;
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: "Turn off" }));

    stored = { enabled: true, blocked_tools: ["browser"] };
    post.mockRejectedValueOnce(refusal(409, "mcp_guardrails_changed", REFUSED));
    await user.click(screen.getByRole("button", { name: "Turn off" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]).toEqual([
      SAVE_URL,
      { config: { ...opened, enabled: false }, base: opened },
    ]);
    await waitFor(() => expect(summary()).toHaveTextContent("browser"));
    // The console's own sentence: this row has no Reload to offer.
    expect(snackbars).toEqual([
      { message: TURN_OFF_CHANGED, options: { variant: "error" } },
    ]);
  });
});

// Opened with the outputs check off and the inputs check absent, which the
// dialog shows off too.
const OPENED_OFF = {
  enabled: false,
  validate_outputs: false,
  blocked_tools: ["shell"],
};

const checkbox = (dialog, name) =>
  within(dialog).getByRole("checkbox", { name });
const saveButton = (dialog) =>
  within(dialog).getByRole("button", { name: "Save stored settings" });

// Another admin saves `theirs`; this person's save is refused, and Reload
// reads theirs.
async function refusedThenReload(user, dialog, theirs) {
  stored = theirs;
  post.mockRejectedValueOnce(refusal(409, "mcp_guardrails_changed", REFUSED));
  await user.click(saveButton(dialog));
  await user.click(
    await within(dialog).findByRole("button", { name: "Reload" }),
  );
  await waitFor(() => expect(within(dialog).queryByText(CHANGED)).toBeNull());
}

describe("after Reload, a field changed and changed back is not this person's change", () => {
  it("a switch turned on and off again shows and saves the other admin's value", async () => {
    stored = OPENED_OFF;
    const user = userEvent.setup();
    renderTab();
    const dialog = await openEditor(user);
    const outputs = () => checkbox(dialog, "Validate tool outputs");
    const inputs = () => checkbox(dialog, "Validate tool inputs");
    // On and off again; Blocked tools is changed.
    await user.click(outputs());
    await user.click(outputs());
    await user.click(inputs());
    await user.click(inputs());
    await user.type(
      within(dialog).getByLabelText("Blocked tools"),
      "{Enter}write",
    );

    // Meanwhile another admin turns both checks on.
    const theirs = {
      ...OPENED_OFF,
      validate_outputs: true,
      validate_inputs: true,
    };
    await refusedThenReload(user, dialog, theirs);
    expect(post.mock.calls[0]).toEqual([
      SAVE_URL,
      {
        config: {
          ...OPENED_OFF,
          validate_inputs: false,
          blocked_tools: ["shell", "write"],
        },
        base: OPENED_OFF,
      },
    ]);

    // Both checks show the other admin's value; the list keeps the edit.
    await waitFor(() => expect(outputs()).toBeChecked());
    expect(inputs()).toBeChecked();
    expect(within(dialog).getByLabelText("Blocked tools")).toHaveValue(
      "shell\nwrite",
    );

    post.mockResolvedValueOnce(SAVED);
    await user.click(saveButton(dialog));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1]).toEqual([
      SAVE_URL,
      {
        config: { ...theirs, blocked_tools: ["shell", "write"] },
        base: theirs,
      },
    ]);
  });

  it("a list retyped to its opened text, and a limit entered as it was, show and save the other admin's", async () => {
    const opened = { ...OPENED_OFF, tool_rate_limits: { crm__search: 5 } };
    stored = opened;
    const user = userEvent.setup();
    renderTab();
    const dialog = await openEditor(user);
    const blocked = () => within(dialog).getByLabelText("Blocked tools");
    const limits = () =>
      within(dialog)
        .getAllByRole("listitem")
        .map((row) => row.textContent);
    // Retyped to the text it opened with, and the same limit removed and
    // added again.
    await user.clear(blocked());
    await user.type(blocked(), "shell");
    await user.click(
      within(dialog).getByRole("button", {
        name: "Remove the limit for crm__search",
      }),
    );
    await user.type(within(dialog).getByLabelText("Tool name"), "crm__search");
    await user.type(within(dialog).getByLabelText("Per minute"), "5");
    await user.click(within(dialog).getByRole("button", { name: "Add limit" }));
    // One real change.
    await user.click(checkbox(dialog, "Validate tool inputs"));

    const theirs = {
      ...opened,
      blocked_tools: ["browser"],
      tool_rate_limits: { crm__search: 9 },
    };
    await refusedThenReload(user, dialog, theirs);

    await waitFor(() => expect(blocked()).toHaveValue("browser"));
    expect(limits()).toEqual(["crm__search9 per minute"]);
    expect(checkbox(dialog, "Validate tool inputs")).toBeChecked();

    post.mockResolvedValueOnce(SAVED);
    await user.click(saveButton(dialog));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1]).toEqual([
      SAVE_URL,
      { config: { ...theirs, validate_inputs: true }, base: theirs },
    ]);
  });

  it("a field this person did change keeps their value through each Reload, measured against what the last Reload read", async () => {
    stored = OPENED_OFF;
    const user = userEvent.setup();
    renderTab();
    const dialog = await openEditor(user);
    const outputs = () => checkbox(dialog, "Validate tool outputs");
    const blocked = () => within(dialog).getByLabelText("Blocked tools");
    await user.clear(blocked());
    await user.type(blocked(), "write");

    // The other admin turns the outputs check on and adds a server.
    const first = {
      ...OPENED_OFF,
      validate_outputs: true,
      allowed_servers: ["crm"],
    };
    await refusedThenReload(user, dialog, first);
    await waitFor(() => expect(outputs()).toBeChecked());
    expect(blocked()).toHaveValue("write");
    expect(within(dialog).getByLabelText("Allowed servers")).toHaveValue("crm");

    // Turned off again: the value it opened with, but a change from what
    // Reload read, so the next Reload keeps it.
    await user.click(outputs());
    const second = { ...first, enabled: true };
    await refusedThenReload(user, dialog, second);
    expect(post.mock.calls[1]).toEqual([
      SAVE_URL,
      {
        config: {
          ...first,
          validate_outputs: false,
          blocked_tools: ["write"],
        },
        base: first,
      },
    ]);
    await waitFor(() =>
      expect(checkbox(dialog, "Enabled in stored settings")).toBeChecked(),
    );
    expect(outputs()).not.toBeChecked();
    expect(blocked()).toHaveValue("write");

    post.mockResolvedValueOnce(SAVED);
    await user.click(saveButton(dialog));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
    expect(post.mock.calls[2]).toEqual([
      SAVE_URL,
      {
        config: {
          ...second,
          validate_outputs: false,
          blocked_tools: ["write"],
        },
        base: second,
      },
    ]);
  });

  it("after two refusals, a switch this person turned on shows the other admin's later off before the save, and the save sends it", async () => {
    stored = OPENED_OFF;
    const user = userEvent.setup();
    renderTab();
    const dialog = await openEditor(user);
    const outputs = () => checkbox(dialog, "Validate tool outputs");
    // Opened off; this person turns it on.
    await user.click(outputs());

    // Another admin saves it on. Reload keeps this person's on, a change from
    // the off the dialog opened with, and measures the next change against
    // what this Reload read.
    const first = { ...OPENED_OFF, validate_outputs: true };
    await refusedThenReload(user, dialog, first);
    expect(post.mock.calls[0]).toEqual([
      SAVE_URL,
      { config: { ...OPENED_OFF, validate_outputs: true }, base: OPENED_OFF },
    ]);
    expect(outputs()).toBeChecked();

    // The other admin then saves it off. This person's on now equals what
    // the last Reload read, so it is not their change: the second Reload
    // shows the other admin's off.
    const second = { ...first, validate_outputs: false };
    await refusedThenReload(user, dialog, second);
    expect(post.mock.calls[1]).toEqual([
      SAVE_URL,
      { config: first, base: first },
    ]);
    await waitFor(() => expect(outputs()).not.toBeChecked());
    // Shown in the dialog before any save.
    expect(post).toHaveBeenCalledTimes(2);

    // The save sends the off, made on what the second Reload read.
    post.mockResolvedValueOnce(SAVED);
    await user.click(saveButton(dialog));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
    expect(post.mock.calls[2]).toEqual([
      SAVE_URL,
      { config: second, base: second },
    ]);
    expect(post.mock.calls[2][1].config.validate_outputs).toBe(false);
    // No third refusal: the dialog closes on the save.
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: DIALOG })).toBeNull(),
    );
    expect(snackbars).toEqual([
      {
        message: "MCP guardrail settings saved",
        options: { variant: "success" },
      },
    ]);
  });
});
