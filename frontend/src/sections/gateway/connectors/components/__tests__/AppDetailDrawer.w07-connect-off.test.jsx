import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "src/utils/test-utils";

import AppDetailDrawer from "../AppDetailDrawer";
import {
  MCP_CONNECT_OFF_NOTICE,
  releaseFeatures,
} from "../../../mcp/hooks/useMCPReleaseFeatures";

const { api, status } = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  status: { current: {} },
}));
vi.mock("src/utils/axios", () => ({
  default: api,
  endpoints: {
    gateway: {
      connectorAdd: (id) => `/agentcc/connectors/apps/${id}/add/`,
      mcpCredentials: "/agentcc/mcp/credentials/",
      connectorOAuthClients: "/agentcc/connectors/oauth-clients/",
      connectorOAuthClient: (id) => `/agentcc/connectors/oauth-clients/${id}/`,
    },
  },
}));
vi.mock("src/config-global", async (importOriginal) => ({
  ...(await importOriginal()),
  HOST_API: "https://api.example.test",
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-w07" }),
}));
vi.mock("../../../mcp/hooks/useMCPConfig", () => ({
  useMCPStatus: () => status.current,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// Real drawer, frame, role tooltip, forms and mutation hooks. Only the HTTP
// boundary/status/context are supplied: no rendered control is a test stub.
function appFor(state) {
  const provider = {
    id: "docs-mcp",
    catalog_ref: "docs/docs-mcp@1.0.0",
    url_template: "https://docs.example.test/mcp",
    credential: { mode: "shared", kind: "api_key", header_name: "X-Api-Key" },
    account_modes: ["agent"],
  };
  if (state === "registration") {
    provider.credential = { mode: "per_user", kind: "oauth" };
    provider.account_modes = ["user"];
    provider.connect = {
      state: "registration_required",
      served: true,
      own_app: {
        client_group: "google",
        redirect_uri: "https://api.example.test/oauth/callback/",
        required: true,
      },
    };
  }
  const added = state === "needs_key" || state === "connected";
  return {
    id: "docs",
    name: "Docs",
    description: "A catalogue drawer test app.",
    default_provider: provider.id,
    providers: [provider],
    servers: added
      ? [
          {
            server_id: "docs",
            provider: provider.id,
            catalog_ref: provider.catalog_ref,
            credential_mode: "shared",
            connected: state === "connected",
          },
        ]
      : [],
  };
}

let client;
beforeEach(() => {
  vi.resetAllMocks();
  api.get.mockResolvedValue({ data: { results: [] } });
  api.post.mockResolvedValue({ data: { result: {} } });
  api.delete.mockResolvedValue({ data: { result: {} } });
  status.current = {
    data: {
      enabled: true,
      features: {
        catalog: true,
        credential_vault: true,
        virtual_servers: true,
      },
    },
  };
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

function tree(app, canManage) {
  return (
    <QueryClientProvider client={client}>
      <AppDetailDrawer
        app={app}
        gatewayId="gw-w07"
        canManage={canManage}
        canConnect={releaseFeatures(status.current).catalogConnect}
        upstreamOAuth
        onClose={() => {}}
        onCategory={() => {}}
      />
    </QueryClientProvider>
  );
}

function expectNoRequests() {
  expect(api.get).not.toHaveBeenCalled();
  expect(api.post).not.toHaveBeenCalled();
  expect(api.delete).not.toHaveBeenCalled();
}

function expectConnectOff() {
  expect(screen.getByText(MCP_CONNECT_OFF_NOTICE)).toBeVisible();
  expect(MCP_CONNECT_OFF_NOTICE).toBe(
    "Connecting catalog apps requires the catalog, credential management and named virtual servers to be on.",
  );
  expect
    .soft(
      screen.queryAllByRole("button", {
        name: /^(Connect|Save key|Disconnect)$/,
      }),
    )
    .toHaveLength(0);
  // MUI's actual role tooltip labels its wrapping span before hover.
  expect
    .soft(screen.queryAllByLabelText("Owner or Admin only"))
    .toHaveLength(0);
  expect.soft(screen.queryByRole("tooltip")).toBeNull();
  expect
    .soft(screen.queryByText(/Ask an Owner or Admin to register it\./))
    .toBeNull();
  expect.soft(screen.queryByText(/Add it below to connect\./)).toBeNull();
  expect
    .soft(
      screen.queryByText(
        "Only an organization Owner or Admin can connect apps.",
      ),
    )
    .toBeNull();
  expect(screen.queryByLabelText("API key")).toBeNull();
  expectNoRequests();
}

const rolesAndStates = [true, false].flatMap((canManage) =>
  ["available", "needs_key", "connected", "registration"].map((state) => [
    canManage,
    state,
  ]),
);

describe.each([
  [false, true],
  [true, false],
  [false, false],
])("W07 catalogue ON, vault=%s virtual=%s", (vault, virtual) => {
  it.each(rolesAndStates)(
    "canManage=%s, %s has only the capability notice, not role-gated actions",
    (canManage, state) => {
      status.current.data.features.credential_vault = vault;
      status.current.data.features.virtual_servers = virtual;
      render(tree(appFor(state), canManage));
      expectConnectOff();
    },
  );
});

describe("W07 connect ON preserves role behavior", () => {
  it.each(
    [true, false].flatMap((canManage) =>
      [
        ["available", "Connect"],
        ["needs_key", "Save key"],
        ["connected", "Disconnect"],
      ].map(([state, label]) => [canManage, state, label]),
    ),
  )(
    "canManage=%s, %s retains %s role behavior",
    async (canManage, state, label) => {
      const user = userEvent.setup();
      render(tree(appFor(state), canManage));
      const button = screen.getByRole("button", { name: label });
      if (state === "needs_key") {
        expect(
          screen.getByText(
            "Docs is added, but its key is not stored, so the gateway cannot call it.",
          ),
        ).toBeVisible();
      }
      if (canManage) {
        expect(button).toBeEnabled();
        expect(screen.queryAllByLabelText("Owner or Admin only")).toHaveLength(
          0,
        );
      } else {
        expect(button).toBeDisabled();
        expect(
          screen.getByText(
            "Only an organization Owner or Admin can connect apps.",
          ),
        ).toBeVisible();
        await user.hover(button.parentElement);
        expect(await screen.findByRole("tooltip")).toHaveTextContent(
          "Owner or Admin only",
        );
      }
      expect(screen.queryByText(MCP_CONNECT_OFF_NOTICE)).toBeNull();
      expectNoRequests();
    },
  );

  it.each([true, false])(
    "canManage=%s keeps truthful registration guidance while ON",
    async (canManage) => {
      render(tree(appFor("registration"), canManage));
      const guidance = canManage
        ? /Add it below to connect\./
        : /Ask an Owner or Admin to register it\./;
      expect(await screen.findByText(guidance)).toBeVisible();
      expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
      expect(screen.queryByText(MCP_CONNECT_OFF_NOTICE)).toBeNull();
      expect(api.post).not.toHaveBeenCalled();
      expect(api.delete).not.toHaveBeenCalled();
    },
  );

  it("an admin can still confirm Disconnect while ON", async () => {
    const user = userEvent.setup();
    render(tree(appFor("connected"), true));
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    const confirm = screen.getByRole("dialog", { name: "Disconnect Docs?" });
    expect(
      within(confirm).getByText(
        "Disconnecting removes the app's servers from the saved configuration and revokes their stored credentials. It removes those servers from virtual-server membership and deletes their empty connector-owned virtual servers. If the gateway does not pick up the change, a warning says so.",
      ),
    ).toBeVisible();
    await user.click(
      within(confirm).getByRole("button", { name: "Disconnect" }),
    );
    await waitFor(() => expect(api.delete).toHaveBeenCalledTimes(1));
    expect(api.delete).toHaveBeenCalledWith(
      "/agentcc/connectors/apps/docs/add/",
    );
  });
});

describe.each([
  ["vault", ["credential_vault"]],
  ["named virtual servers", ["virtual_servers"]],
  ["vault and named virtual servers", ["credential_vault", "virtual_servers"]],
])("W07 retained drawer state with %s OFF", (_label, dependencies) => {
  it("dismisses an open Disconnect confirmation and does not restore it on re-enable", async () => {
    const user = userEvent.setup();
    const app = appFor("connected");
    const view = render(tree(app, true));
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(
      screen.getByRole("dialog", { name: "Disconnect Docs?" }),
    ).toBeVisible();

    dependencies.forEach((dependency) => {
      status.current.data.features[dependency] = false;
    });
    view.rerender(tree(app, true));
    expect(
      screen.queryByRole("dialog", { name: "Disconnect Docs?" }),
    ).toBeNull();
    expectConnectOff();

    dependencies.forEach((dependency) => {
      status.current.data.features[dependency] = true;
    });
    view.rerender(tree(app, true));
    expect(
      screen.queryByRole("dialog", { name: "Disconnect Docs?" }),
    ).toBeNull();
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(
      screen.getByRole("dialog", { name: "Disconnect Docs?" }),
    ).toBeVisible();
    expectNoRequests();
  });

  it.each(
    [true, false].flatMap((canManage) =>
      ["per_user", "shared", "retired"].map((mode) => [canManage, mode]),
    ),
  )(
    "canManage=%s, %s status does not direct readers to hidden actions",
    (canManage, mode) => {
      const app = appFor("needs_key");
      if (mode === "retired") {
        app.providers = [];
      } else {
        app.providers[0].credential = { mode, kind: "oauth" };
        app.providers[0].connect = { state: "ready", served: true };
        app.servers[0].credential_mode = mode;
        if (mode === "per_user") {
          app.servers[0].start_url = "/agentcc/mcp/connect/docs/start/";
        } else {
          app.servers[0].connect_url = "/agentcc/mcp/connect/docs/";
        }
      }
      const view = render(tree(app, canManage));
      const onText =
        mode === "retired"
          ? /An organization Owner or Admin can disconnect it\./
          : mode === "per_user"
            ? /Sign in to your own Docs account/
            : canManage
              ? /Sign in to the organization's Docs account/
              : /an Owner or Admin has not signed in/;
      expect(screen.getByText(onText)).toBeVisible();

      dependencies.forEach((dependency) => {
        status.current.data.features[dependency] = false;
      });
      view.rerender(tree(app, canManage));
      expectConnectOff();
      expect(
        screen.queryByRole("button", { name: "Sign in to Docs" }),
      ).toBeNull();
      expect(
        screen.queryByText(/Sign in to (your own|the organization's)/),
      ).toBeNull();
      expect(
        screen.queryByText(/an Owner or Admin has not signed in/),
      ).toBeNull();
      expect(
        screen.queryByText(
          /An organization Owner or Admin can disconnect it\./,
        ),
      ).toBeNull();
      expect(
        screen.getByText(
          mode === "retired"
            ? "Docs uses a provider the catalog no longer offers."
            : mode === "per_user"
              ? "Docs is added."
              : "Docs is added, but the catalog does not report a connected account.",
        ),
      ).toBeVisible();

      dependencies.forEach((dependency) => {
        status.current.data.features[dependency] = true;
      });
      view.rerender(tree(app, canManage));
      expect(screen.getByText(onText)).toBeVisible();
      if (mode === "per_user" || (mode === "shared" && canManage)) {
        expect(
          screen.getByRole("button", { name: "Sign in to Docs" }),
        ).toBeEnabled();
      }
      expectNoRequests();
    },
  );
});

it("forgets an open Disconnect confirmation when the app drawer is dismissed", async () => {
  const user = userEvent.setup();
  const app = appFor("connected");
  const view = render(tree(app, true));
  await user.click(screen.getByRole("button", { name: "Disconnect" }));
  expect(
    screen.getByRole("dialog", { name: "Disconnect Docs?" }),
  ).toBeVisible();
  view.rerender(tree(null, true));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  view.rerender(tree(app, true));
  expect(screen.queryByRole("dialog", { name: "Disconnect Docs?" })).toBeNull();
  expect(screen.getByRole("button", { name: "Disconnect" })).toBeEnabled();
  expectNoRequests();
});

it.each(["available", "needs_key", "connected"])(
  "an open %s drawer removes its actions when a dependency turns OFF",
  (state) => {
    const app = appFor(state);
    const view = render(tree(app, true));
    expect(screen.queryByText(MCP_CONNECT_OFF_NOTICE)).toBeNull();
    status.current.data.features.credential_vault = false;
    view.rerender(tree(app, true));
    expectConnectOff();
  },
);
