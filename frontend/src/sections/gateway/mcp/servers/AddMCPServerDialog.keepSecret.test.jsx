import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderMcp } from "../testing/renderMcp";
import AddMCPServerDialog from "./AddMCPServerDialog";

// An edit keeps a stored secret the person leaves blank: it is never shown
// or sent back, so changing another field, such as the API-key header, does
// not take the secret typed again. A typed one replaces it.
const calls = vi.hoisted(() => ({ save: vi.fn(), probe: vi.fn() }));
const { snackbars } = vi.hoisted(() => ({ snackbars: [] }));
vi.mock("../hooks/useMCPConfig", () => ({
  useUpdateMCPServer: () => ({ mutate: calls.save, isPending: false }),
}));
vi.mock("../hooks/useMCPProbe", () => ({
  useMCPProbe: () => ({ mutateAsync: calls.probe, reset: vi.fn() }),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

const SERVER_URL = "https://crm.example.test/mcp";
const STORED_KEY = "stored-api-key-value-0007";
const KEEP_HELP =
  /A credential is stored and never shown\. Leave this blank to keep it; typing a new one replaces it\./;

function open(editServer) {
  return renderMcp(
    <AddMCPServerDialog
      open
      onClose={vi.fn()}
      gatewayId="default"
      editServer={editServer}
    />,
  );
}

function vaultServer() {
  return {
    serverId: "crm",
    config: {
      url: SERVER_URL,
      transport: "http",
      credential: { mode: "shared", credential_id: "c1", state: "active" },
    },
    hasCredential: true,
    storedHeader: "",
  };
}

async function chooseAuth(user, name) {
  await user.click(screen.getByRole("combobox", { name: /Authentication/ }));
  await user.click(await screen.findByRole("option", { name }));
}

beforeEach(() => {
  calls.save.mockReset();
  calls.probe.mockReset();
  calls.probe.mockResolvedValue({ ok: true, tool_count: 1 });
  snackbars.length = 0;
});

describe("editing a server with a stored secret", () => {
  it("renames the API-key header with the key left blank, keeping the stored key", async () => {
    const user = userEvent.setup();
    open(vaultServer());
    await chooseAuth(user, "API key (replace the stored one)");
    expect(screen.getByLabelText(/^Key/)).toHaveValue("");

    await user.type(screen.getByLabelText(/Header name/), "X-CRM-Key");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(snackbars).toEqual([]);
    expect(calls.save).toHaveBeenCalledExactlyOnceWith(
      {
        gatewayId: "default",
        serverId: "crm",
        config: {
          url: SERVER_URL,
          transport: "http",
          auth: { type: "api_key", header: "X-CRM-Key" },
        },
      },
      expect.any(Object),
    );
  });

  it("keeps a stored token left blank while another field changes", async () => {
    const user = userEvent.setup();
    open(vaultServer());
    await chooseAuth(user, "Bearer token (replace the stored one)");
    const token = screen.getByLabelText(/^Token/);
    expect(token).toHaveValue("");
    expect(token).toHaveAttribute("placeholder", "Stored, not shown");
    expect(screen.getByText(KEEP_HELP)).toBeInTheDocument();

    const moved = "https://crm.example.test/v2/mcp";
    fireEvent.change(screen.getByLabelText(/URL/), {
      target: { value: moved },
    });
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(snackbars).toEqual([]);
    expect(calls.save.mock.calls[0][0].config).toEqual({
      url: moved,
      transport: "http",
    });
  });

  it("shows that a key is stored without its value, and keeps it on a plain save", async () => {
    const user = userEvent.setup();
    open({
      serverId: "search",
      config: {
        url: SERVER_URL,
        transport: "http",
        auth: { type: "api_key", header: "X-Search-Key", key: STORED_KEY },
      },
    });
    await chooseAuth(user, "API key (replace the stored one)");

    const key = screen.getByLabelText(/^Key/);
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("placeholder", "Stored, not shown");
    expect(screen.getByText(KEEP_HELP)).toBeInTheDocument();
    expect(screen.getByLabelText(/Header name/)).toHaveValue("X-Search-Key");
    expect(document.body.innerHTML).not.toContain(STORED_KEY);

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(snackbars).toEqual([]);
    expect(calls.save.mock.calls[0][0].config).toEqual({
      url: SERVER_URL,
      transport: "http",
    });
  });

  it("tests the connection with the stored credential when the secret is left blank", async () => {
    const user = userEvent.setup();
    open(vaultServer());
    await chooseAuth(user, "Bearer token (replace the stored one)");
    await user.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() => expect(calls.probe).toHaveBeenCalled());
    expect(calls.probe.mock.calls[0][0]).toEqual({
      url: SERVER_URL,
      transport: "http",
      credential_id: "c1",
    });
  });
});
