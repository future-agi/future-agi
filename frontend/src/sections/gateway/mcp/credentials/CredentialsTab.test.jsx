import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import { renderMcp, DEMO_MCP } from "../testing/renderMcp";
import { MCP_API } from "../api";
import CredentialsTab from "./CredentialsTab";

const org = vi.hoisted(() => ({ level: 8 }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));

vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});

// Every snackbar the tab raises. A write test ends only once its success
// snackbar has landed: the mutation's tail (re-read, snackbar) must not run
// after the test environment is torn down.
const snackbars = vi.hoisted(() => []);
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
  rows: [],
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: api.get,
      post: api.post,
      delete: api.delete,
    },
  };
});

const CREDENTIAL = {
  id: "5b0b3c2e-1d1f-4b8e-9b1a-6f0f2b9d7a11",
  server_id: "crm-tools",
  scope: "shared",
  kind: "api_key",
  header_name: "X-CRM-Key",
  status: "active",
  version: 2,
  label: "CRM prod",
  created_at: "2026-10-18T10:00:00Z",
  rotated_at: "2026-10-19T10:00:00Z",
  last_used_at: null,
  has_value: true,
};

function succeeded(message) {
  return waitFor(() =>
    expect(snackbars).toContainEqual({
      message,
      options: { variant: "success" },
    }),
  );
}

beforeEach(() => {
  org.level = 8;
  snackbars.length = 0;
  api.rows = [CREDENTIAL];
  api.get.mockReset();
  api.post.mockReset();
  api.delete.mockReset();
  api.get.mockImplementation(async (url) => {
    if (url === "/agentcc/gateways/gw-1/config/") {
      return { data: { result: { mcp: DEMO_MCP } } };
    }
    if (url === MCP_API.credentials) {
      return {
        data: {
          count: api.rows.length,
          next: null,
          previous: null,
          total_pages: 1,
          current_page: 1,
          results: api.rows,
        },
      };
    }
    throw new Error(`unexpected GET ${url}`);
  });
  api.post.mockResolvedValue({
    data: { status: true, result: { ...CREDENTIAL, gateway_synced: true } },
  });
  api.delete.mockResolvedValue({
    data: {
      status: true,
      result: { ...CREDENTIAL, status: "revoked", gateway_synced: true },
    },
  });
});

describe("CredentialsTab", () => {
  it("lists the vault's credentials without ever a value", async () => {
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    const row = await screen.findByRole("row");
    expect(within(row).getByText("crm-tools")).toBeInTheDocument();
    expect(within(row).getByText("API key")).toBeInTheDocument();
    expect(within(row).getByText("X-CRM-Key")).toBeInTheDocument();
    expect(within(row).getByText("CRM prod")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
    expect(document.body.innerHTML).not.toMatch(/has_value/);
  });

  it("stores a new bearer token for a server through the vault", async () => {
    api.rows = [];
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    await screen.findByText("No stored credentials");
    fireEvent.click(
      screen.getAllByRole("button", { name: "Add credential" })[0],
    );
    const dialog = await screen.findByRole("dialog");

    fireEvent.mouseDown(
      within(dialog).getByRole("combobox", { name: /^Server/ }),
    );
    fireEvent.click(
      await screen.findByRole("option", { name: "support-tools" }),
    );
    fireEvent.change(within(dialog).getByLabelText(/^Token/), {
      target: { value: "tok_123" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Store credential" }),
    );

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(MCP_API.credentials, {
        server_id: "support-tools",
        kind: "bearer",
        value: "tok_123",
        label: "",
      }),
    );
    await succeeded("Credential stored for support-tools");
  });

  it("sends an API key with its header name", async () => {
    api.rows = [];
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    await screen.findByText("No stored credentials");
    fireEvent.click(
      screen.getAllByRole("button", { name: "Add credential" })[0],
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.mouseDown(
      within(dialog).getByRole("combobox", { name: /^Server/ }),
    );
    fireEvent.click(await screen.findByRole("option", { name: "crm-tools" }));
    fireEvent.click(within(dialog).getByRole("radio", { name: "API key" }));
    fireEvent.change(
      within(dialog).getByRole("textbox", { name: "Header name" }),
      {
        target: { value: "X-CRM-Key" },
      },
    );
    fireEvent.change(within(dialog).getByLabelText(/^Key/), {
      target: { value: "k_1" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Label" }), {
      target: { value: "CRM prod" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Store credential" }),
    );
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(MCP_API.credentials, {
        server_id: "crm-tools",
        kind: "api_key",
        value: "k_1",
        header_name: "X-CRM-Key",
        label: "CRM prod",
      }),
    );
    await succeeded("Credential stored for crm-tools");
  });

  it("does not offer a server that already has an active credential", async () => {
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    await screen.findByRole("row");
    fireEvent.click(screen.getByRole("button", { name: "Add credential" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.mouseDown(
      within(dialog).getByRole("combobox", { name: /^Server/ }),
    );
    expect(
      await screen.findByRole("option", { name: /crm-tools/ }),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("rotates a credential in place", async () => {
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    const row = await screen.findByRole("row");
    fireEvent.click(within(row).getByRole("button", { name: "Rotate" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/New API key/), {
      target: { value: "k_2" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Rotate" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        MCP_API.rotateCredential(CREDENTIAL.id),
        {
          value: "k_2",
        },
      ),
    );
    await succeeded("Credential rotated");
  });

  it("revokes a credential after confirming", async () => {
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    const row = await screen.findByRole("row");
    fireEvent.click(within(row).getByRole("button", { name: "Revoke" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Revoke credential" }),
    );
    await waitFor(() =>
      expect(api.delete).toHaveBeenCalledWith(
        MCP_API.credential(CREDENTIAL.id),
      ),
    );
    await succeeded("Credential for crm-tools revoked");
  });

  it("shows the API's refusal in the dialog", async () => {
    api.rows = [];
    api.post.mockRejectedValueOnce({
      response: {
        status: 403,
        data: { detail: "You do not have permission to perform this action." },
      },
    });
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    await screen.findByText("No stored credentials");
    fireEvent.click(
      screen.getAllByRole("button", { name: "Add credential" })[0],
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.mouseDown(
      within(dialog).getByRole("combobox", { name: /^Server/ }),
    );
    fireEvent.click(
      await screen.findByRole("option", { name: "support-tools" }),
    );
    fireEvent.change(within(dialog).getByLabelText(/^Token/), {
      target: { value: "tok" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Store credential" }),
    );
    expect(
      await within(dialog).findByText(
        "You do not have permission to perform this action.",
      ),
    ).toBeInTheDocument();
  });

  it("explains the vault when it is empty", async () => {
    api.rows = [];
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    expect(
      await screen.findByText("No stored credentials"),
    ).toBeInTheDocument();
  });
});

describe("credential audit regressions", () => {
  it("warns on persisted revocation without claiming gateway removal", async () => {
    api.delete.mockImplementation(async () => {
      api.rows = [{ ...CREDENTIAL, status: "revoked" }];
      return { data: { result: { ...api.rows[0], gateway_synced: false } } };
    });
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/successful sync/i);
    expect(dialog).toHaveTextContent(/does not invalidate.*vendor/i);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Revoke credential" }),
    );
    await waitFor(() => expect(snackbars).toHaveLength(1));
    expect(snackbars[0].options.variant).toBe("warning");
    expect(snackbars[0].message).toMatch(/revocation.*saved/i);
    expect(snackbars[0].message).toMatch(/may still send.*successful sync/i);
    expect(snackbars[0].message).toMatch(/reload/i);
    expect(await screen.findByText("Revoked")).toBeInTheDocument();
    expect(
      api.get.mock.calls.filter(([url]) => url === MCP_API.credentials).length,
    ).toBeGreaterThan(1);
    expect(
      api.get.mock.calls.filter(([url]) => url.endsWith("/config/")).length,
    ).toBeGreaterThan(1);
    expect(api.delete).toHaveBeenCalledTimes(1);
  });

  it.each([1, 3, null])(
    "keeps role %s read-only before a form opens",
    async (level) => {
      org.level = level;
      renderMcp(<CredentialsTab gatewayId="gw-1" />);
      await screen.findByRole("row");
      for (const name of ["Add credential", "Rotate", "Revoke"]) {
        expect(screen.getByRole("button", { name })).toBeDisabled();
        fireEvent.click(screen.getByRole("button", { name }));
      }
      expect(screen.getByText("Owner or Admin only")).toBeInTheDocument();
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(api.post).not.toHaveBeenCalled();
      expect(api.delete).not.toHaveBeenCalled();
    },
  );

  it("keeps empty vault creation visible but disabled for a Member", async () => {
    org.level = 3;
    api.rows = [];
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    await screen.findByText("No stored credentials");
    const button = screen.getByRole("button", { name: "Add credential" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.getByText("Owner or Admin only")).toBeInTheDocument();
  });
});

describe("WALK-OAUTH account actions", () => {
  it.each(["active", "needs_reauth", "revoked"])(
    "routes a personal OAuth account (%s) to its own management flow",
    async (status) => {
      api.rows = [{ ...CREDENTIAL, kind: "oauth", scope: "user", status }];
      renderMcp(<CredentialsTab gatewayId="gw-1" />);
      const row = await screen.findByRole("row");
      expect(within(row).queryByRole("button", { name: "Rotate" })).toBeNull();
      expect(within(row).queryByRole("button", { name: "Revoke" })).toBeNull();
      expect(within(row).getByRole("link", { name: "Manage your account" }))
        .toHaveAttribute("href", "/dashboard/gateway/integrations?connect=crm-tools");
      expect(api.post).not.toHaveBeenCalled();
      expect(api.delete).not.toHaveBeenCalled();
    },
  );

  it("routes shared OAuth management to Integrations, never static rotation", async () => {
    api.rows = [{ ...CREDENTIAL, kind: "oauth", scope: "shared" }];
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    const row = await screen.findByRole("row");
    expect(within(row).queryByRole("button", { name: "Rotate" })).toBeNull();
    expect(within(row).queryByRole("button", { name: "Revoke" })).toBeNull();
    expect(within(row).getByRole("link", { name: "Manage shared account" }))
      .toHaveAttribute("href", "/dashboard/gateway/integrations");
  });

  it("lets a Member navigate to their own OAuth account", async () => {
    org.level = 3;
    api.rows = [{ ...CREDENTIAL, kind: "oauth", scope: "user" }];
    renderMcp(<CredentialsTab gatewayId="gw-1" />);
    expect(await screen.findByRole("link", { name: "Manage your account" }))
      .toHaveAttribute("href", "/dashboard/gateway/integrations?connect=crm-tools");
  });
});

// W07 enabled-scenario fixture; the release-switch specs exercise OFF.
vi.mock("../hooks/useMCPReleaseFeatures", () => ({
  useMCPReleaseFeatures: () => ({
    catalog: true,
    credentialVault: true,
    virtualServers: true,
    catalogConnect: true,
  }),
  useMCPReleaseGuard: () => () => {},
  // The status has answered: writes are not held.
  useMCPWriteHold: () => ({ held: false, failed: false, retry: () => {} }),
}));
