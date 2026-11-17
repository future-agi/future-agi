import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderMcp } from "../testing/renderMcp";
import AddMCPServerDialog from "./AddMCPServerDialog";

const calls = vi.hoisted(() => ({ save: vi.fn(), probe: vi.fn() }));
vi.mock("../hooks/useMCPConfig", () => ({
  useUpdateMCPServer: () => ({ mutate: calls.save, isPending: false }),
}));
vi.mock("../hooks/useMCPProbe", () => ({
  useMCPProbe: () => ({ mutateAsync: calls.probe, reset: vi.fn() }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const CREDENTIAL = Object.freeze({
  mode: "shared", credential_id: "c1", state: "active",
});
const REPLACEMENT_TOKEN = "mcp-edit-test-token-not-a-real-credential";

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

async function chooseAuth(user, name) {
  await user.click(screen.getByRole("combobox", { name: /Authentication/ }));
  await user.click(await screen.findByRole("option", { name }));
}

beforeEach(() => {
  calls.save.mockReset();
  calls.probe.mockReset();
  calls.probe.mockResolvedValue({ ok: true, tool_count: 2 });
});

describe("J15.W15-F01: editing a saved SSE server", () => {
  it.each(["keep", "replace", "remove"])(
    "preserves SSE in probe and save when choosing to %s the credential",
    async (choice) => {
      const user = userEvent.setup();
      const config = Object.freeze({
        url: "https://legacy.example.test/sse",
        transport: "sse",
        era: "2024-11-05",
        credential: CREDENTIAL,
      });
      open({ serverId: "legacy", config });
      let probeAuth = { credential_id: "c1" };
      let saveAuth = {};
      if (choice === "replace") {
        await chooseAuth(user, "Bearer token (replace the stored one)");
        fireEvent.change(screen.getByLabelText(/^Token/), {
          target: { value: REPLACEMENT_TOKEN },
        });
        probeAuth = { auth: { type: "bearer", value: REPLACEMENT_TOKEN } };
        saveAuth = { auth: { type: "bearer", token: REPLACEMENT_TOKEN } };
      } else if (choice === "remove") {
        await chooseAuth(user, /Remove the stored credential/);
        probeAuth = {};
        saveAuth = { auth: { type: "none" } };
      }

      await user.click(screen.getByRole("button", { name: "Test connection" }));
      await waitFor(() => expect(calls.probe).toHaveBeenCalledWith({
        url: config.url, transport: "sse", ...probeAuth,
      }));
      expect(calls.save).not.toHaveBeenCalled();
      await user.click(screen.getByRole("button", { name: "Save" }));
      expect(calls.save).toHaveBeenCalledExactlyOnceWith(
        {
          gatewayId: "default",
          serverId: "legacy",
          config: {
            url: config.url, transport: "sse", era: "2024-11-05", ...saveAuth,
          },
        },
        expect.any(Object),
      );
      expect(config.credential).toEqual(CREDENTIAL);
    },
  );

  it.each(["http", undefined])(
    "keeps HTTP behavior for a saved transport of %s",
    async (transport) => {
      const config = { url: "https://example.test/mcp" };
      if (transport) config.transport = transport;
      open({ serverId: "remote", config });
      fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
      await waitFor(() => expect(calls.probe).toHaveBeenCalledWith({
        url: config.url, transport: "http",
      }));
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      expect(calls.save.mock.calls[0][0].config).toEqual({
        url: config.url, transport: "http",
      });
    },
  );
});

describe("J15.W15-F02: editing a catalog-connected server", () => {
  const settings = Object.freeze({
    url: "https://gitlab.example.test/mcp",
    transport: "http",
    enabled: false,
    era: "auto",
    timeout_ms: 12000,
    forward_headers: Object.freeze(["x-tenant"]),
    labels: Object.freeze(["private_data"]),
    propagate_depth: false,
    trusted_annotations: false,
    catalog_ref: "gitlab/pat@1.0.0",
    url_variables: Object.freeze({ host: "gitlab.example.test" }),
    toolkits: Object.freeze(["gitlab"]),
    oauth: Object.freeze({ adapter: "auto" }),
    tools_cache_ttl_seconds: 0,
  });

  it.each(["keep", "replace", "remove"])(
    "retains catalog identity and connection settings when choosing to %s the credential",
    async (choice) => {
      const user = userEvent.setup();
      const config = Object.freeze({ ...settings, credential: CREDENTIAL });
      open({ serverId: "gitlab", config });
      let auth = {};
      if (choice === "replace") {
        await chooseAuth(user, "Bearer token (replace the stored one)");
        fireEvent.change(screen.getByLabelText(/^Token/), {
          target: { value: REPLACEMENT_TOKEN },
        });
        auth = { auth: { type: "bearer", token: REPLACEMENT_TOKEN } };
      } else if (choice === "remove") {
        await chooseAuth(user, /Remove the stored credential/);
        auth = { auth: { type: "none" } };
      }
      await user.click(screen.getByRole("button", { name: "Save" }));
      expect(calls.save).toHaveBeenCalledExactlyOnceWith(
        {
          gatewayId: "default",
          serverId: "gitlab",
          config: { ...settings, ...auth },
        },
        expect.any(Object),
      );
      expect(calls.save.mock.calls[0][0].config).not.toHaveProperty("credential");
      expect(calls.probe).not.toHaveBeenCalled();
    },
  );

  it("preserves metadata while changing the URL path and restoring a disabled server", () => {
    open({
      serverId: "gitlab",
      config: Object.freeze({ ...settings, credential: CREDENTIAL }),
    });
    const url = "https://gitlab.example.test/new/mcp";
    fireEvent.change(screen.getByLabelText(/URL/), { target: { value: url } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Enabled" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(calls.save.mock.calls[0][0].config).toEqual({
      ...settings, url, enabled: true,
    });
    expect(calls.probe).not.toHaveBeenCalled();
  });
});
