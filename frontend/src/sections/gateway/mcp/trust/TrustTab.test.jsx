import React from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import axios from "src/utils/axios";
import TrustTab from "./TrustTab";
import { oldHash, newHash, revision } from "./revisionFixture";

const state = vi.hoisted(() => ({
  hidden: false,
  organizationId: "org-a",
  canEdit: true,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: { gateway: {} },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: state.organizationId }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({
    canEdit: state.canEdit,
    disabledReason: "Only an organization owner or admin can change this.",
  }),
}));
vi.mock("../../shell/page-status", () => ({
  isMcpTabHidden: () => state.hidden,
}));

const REVISIONS = "/agentcc/mcp/revisions/";
const CONFIG = "/agentcc/gateways/gw-1/config/";
const UPDATE = "/agentcc/gateways/gw-1/update-config/";
let rows;
let config;
let synced;
let clients;
const theme = createTheme({ palette: palette("light"), typography });
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  const tree = () => (
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <TrustTab gatewayId="gw-1" />
      </ThemeProvider>
    </QueryClientProvider>
  );
  return { ...render(tree()), tree };
}
async function ready() {
  return screen.findByRole("article", { name: "orders / lookup revision" });
}
async function confirm(action) {
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(
    within(dialog).getByRole(
      "button",
      /** @type {import("@testing-library/react").ByRoleOptions} */ ({
        name: action,
        exact: true,
      }),
    ),
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(state, {
    hidden: false,
    organizationId: "org-a",
    canEdit: true,
  });
  rows = [revision];
  synced = true;
  clients = [];
  config = {
    mcp: {
      servers: { orders: {} },
      pinning: { pins: { "orders/lookup": oldHash, "other/tool": oldHash } },
    },
  };
  vi.mocked(axios.get).mockImplementation(async (url) => {
    if (url === REVISIONS)
      return {
        data: {
          status: true,
          result: { revisions: rows, gateway_synced: synced },
        },
      };
    if (url === CONFIG) return { data: { status: true, result: config } };
    throw new Error(`Unexpected GET ${url}`);
  });
  vi.mocked(axios.post).mockImplementation(async (url) => {
    if (url.endsWith("/approve/") || url.endsWith("/reject/")) {
      rows = [];
      return {
        data: {
          status: true,
          result: {
            ...revision,
            status: url.endsWith("/approve/") ? "approved" : "rejected",
            gateway_synced: true,
          },
        },
      };
    }
    if (url === UPDATE)
      return {
        data: { status: true, result: { version: 2, gateway_synced: true } },
      };
    throw new Error(`Unexpected POST ${url}`);
  });
});
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
});

describe("tool revision review", () => {
  it("can re-pin an approved definition even with no pending revisions", async () => {
    rows = [];
    config.mcp.pinning.pins = {};
    const originalGet = vi.mocked(axios.get).getMockImplementation();
    vi.mocked(axios.get).mockImplementation((url, options) =>
      options?.params?.status === "approved"
        ? Promise.resolve({
            data: {
              status: true,
              result: {
                revisions: [{ ...revision, status: "approved" }],
                gateway_synced: true,
              },
            },
          })
        : /** @type {NonNullable<typeof originalGet>} */ (originalGet)(
            url,
            options,
          ),
    );
    mount();
    const accepted = await screen.findByRole("article", {
      name: "orders / lookup accepted definition",
    });
    fireEvent.click(within(accepted).getByRole("button", { name: "Pin" }));
    await confirm("Pin");
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        UPDATE,
        { mcp: { pinning: { pins: { "orders/lookup": newHash } } } },
        { timeout: 30000, headers: { "X-Organization-Id": "org-a" } },
      ),
    );
  });
  it("preserves the W09 fallback and issues no requests behind the OFF release switch", () => {
    state.hidden = true;
    mount();
    expect(screen.getByText("Not available on this gateway")).toBeVisible();
    expect(
      screen.getByText(
        "This gateway does not review changes to a server's tools.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Tool revisions" }),
    ).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(axios.get).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });
  it("waits for organization context without sending an unscoped request", () => {
    state.organizationId = null;
    mount();
    expect(axios.get).not.toHaveBeenCalled();
  });
  it("loads pending revisions and config with the captured organization header", async () => {
    mount();
    await ready();
    expect(axios.get).toHaveBeenCalledWith(
      REVISIONS,
      expect.objectContaining({
        params: { status: "pending" },
        headers: { "X-Organization-Id": "org-a" },
      }),
    );
    expect(axios.get).toHaveBeenCalledWith(
      CONFIG,
      expect.objectContaining({ headers: { "X-Organization-Id": "org-a" } }),
    );
    expect(screen.getByText("Looks up orders")).toBeVisible();
    expect(screen.getByText("Looks up and retains orders")).toBeVisible();
    expect(screen.getByText("inputSchema.required")).toBeVisible();
    expect(screen.getAllByText("Not present").length).toBeGreaterThan(0);
    expect(
      screen.getByText('[\n  "id"\n]', { normalizer: (text) => text }),
    ).toBeVisible();
  });
  it("shows loading then an empty state", async () => {
    /** @type {(value: unknown) => void} */
    let resolve;
    vi.mocked(axios.get).mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    );
    rows = [];
    mount();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading tool revisions",
    );
    resolve({
      data: { status: true, result: { revisions: [], gateway_synced: true } },
    });
    expect(await screen.findByText("No pending tool revisions.")).toBeVisible();
  });
  it("shows a retryable load error rather than a misleading empty state", async () => {
    vi.mocked(axios.get).mockRejectedValueOnce(new Error("offline fixture"));
    mount();
    expect(
      await screen.findByText(/Could not load tool revisions/),
    ).toBeVisible();
    expect(screen.queryByText("No pending tool revisions.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await ready();
  });
  it("warns about stale gateway data and disables decisions", async () => {
    synced = false;
    mount();
    const row = await ready();
    expect(screen.getByText(/last synchronized revisions/i)).toBeVisible();
    expect(within(row).getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(within(row).getByRole("button", { name: "Reject" })).toBeDisabled();
  });
  it.each(["Approve", "Reject"])(
    "confirms %s and posts only the decision with org scope",
    async (action) => {
      mount();
      const row = await ready();
      fireEvent.click(within(row).getByRole("button", { name: action }));
      expect(axios.post).not.toHaveBeenCalled();
      await confirm(action);
      await waitFor(() =>
        expect(axios.post).toHaveBeenCalledWith(
          `${REVISIONS}17/${action.toLowerCase()}/`,
          {},
          { timeout: 30000, headers: { "X-Organization-Id": "org-a" } },
        ),
      );
      expect(
        await screen.findByText("No pending tool revisions."),
      ).toBeVisible();
    },
  );
  it("keeps mutation failures visible and never reports approval success", async () => {
    vi.mocked(axios.post).mockRejectedValue({
      response: {
        status: 409,
        data: {
          message: "Revision already decided. Refresh and review again.",
        },
      },
    });
    mount();
    fireEvent.click(
      within(await ready()).getByRole("button", { name: "Approve" }),
    );
    await confirm("Approve");
    expect(
      await screen.findByText(
        "Revision already decided. Refresh and review again.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("Revision approved and pinned.")).toBeNull();
  });
  it("does not turn saved-but-not-applied into a success", async () => {
    vi.mocked(axios.post).mockResolvedValue({
      data: {
        status: true,
        result: { ...revision, status: "approved", gateway_synced: false },
      },
    });
    mount();
    fireEvent.click(
      within(await ready()).getByRole("button", { name: "Approve" }),
    );
    await confirm("Approve");
    expect(await screen.findByText(/saved.*not.*applied/i)).toBeVisible();
    expect(screen.queryByText("Revision approved and pinned.")).toBeNull();
  });
  it("fails closed on a 200 response that does not confirm a decision", async () => {
    vi.mocked(axios.post).mockResolvedValue({
      data: { status: false, result: {} },
    });
    mount();
    fireEvent.click(
      within(await ready()).getByRole("button", { name: "Approve" }),
    );
    await confirm("Approve");
    expect(await screen.findByText(/did not confirm/i)).toBeVisible();
  });
  it("exposes review as read-only for a non-admin", async () => {
    state.canEdit = false;
    mount();
    const row = await ready();
    expect(within(row).getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(within(row).getByRole("button", { name: "Reject" })).toBeDisabled();
    screen
      .getAllByRole("button", { name: "Unpin" })
      .forEach((button) => expect(button).toBeDisabled());
    expect(axios.post).not.toHaveBeenCalled();
  });
  it("pins the reviewed accepted definition, not the held candidate", async () => {
    config.mcp.pinning.pins = {};
    mount();
    fireEvent.click(
      within(await ready()).getByRole("button", {
        name: "Pin accepted revision",
      }),
    );
    await confirm("Pin");
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        UPDATE,
        { mcp: { pinning: { pins: { "orders/lookup": oldHash } } } },
        { timeout: 30000, headers: { "X-Organization-Id": "org-a" } },
      ),
    );
  });
  it("unpins just the selected tool and discloses disabling auto-baseline for the last server pin", async () => {
    mount();
    await ready();
    const pin = screen.getByRole("article", { name: "orders/lookup pin" });
    fireEvent.click(within(pin).getByRole("button", { name: "Unpin" }));
    expect(
      await screen.findByText(
        /disables automatic baselines for this organization/i,
      ),
    ).toBeVisible();
    await confirm("Unpin");
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        UPDATE,
        {
          mcp: {
            pinning: { pins: { "orders/lookup": null }, auto_baseline: false },
          },
        },
        { timeout: 30000, headers: { "X-Organization-Id": "org-a" } },
      ),
    );
  });
  it("preserves auto-baseline when another tool on that server stays pinned", async () => {
    config.mcp.pinning.pins["orders/other"] = oldHash;
    mount();
    await ready();
    fireEvent.click(
      within(
        screen.getByRole("article", { name: "orders/lookup pin" }),
      ).getByRole("button", { name: "Unpin" }),
    );
    await confirm("Unpin");
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        UPDATE,
        { mcp: { pinning: { pins: { "orders/lookup": null } } } },
        expect.anything(),
      ),
    );
  });
  it("drops the confirmation and old rows on an organization switch", async () => {
    const view = mount();
    fireEvent.click(
      within(await ready()).getByRole("button", { name: "Approve" }),
    );
    state.organizationId = "org-b";
    rows = [];
    view.rerender(view.tree());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(await screen.findByText("No pending tool revisions.")).toBeVisible();
    expect(axios.get).toHaveBeenCalledWith(
      REVISIONS,
      expect.objectContaining({ headers: { "X-Organization-Id": "org-b" } }),
    );
    expect(axios.post).not.toHaveBeenCalled();
  });
});
