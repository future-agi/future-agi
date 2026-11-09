import React from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import KeyManagementSection from "../KeyManagementSection";
import { faTestTheme } from "../../__tests__/faTheme";
import { expectAdminOnly } from "../../testing/adminOnly";
import { lastGrid } from "../../providers/__tests__/grid-mock";
import {
  CELL_PADDING_PX,
  CELL_TEXT_PX,
  gridBudget1440,
  minimumGridWidth,
} from "../../logs/__tests__/gridFit";

const state = vi.hoisted(() => ({
  role: "Owner",
  keys: [],
  error: null,
  refetch: vi.fn(),
  create: vi.fn(),
  createResult: null,
  rotate: vi.fn(),
  rotateResult: null,
  revoke: vi.fn(),
  revokeResult: null,
  sync: vi.fn(),
  credits: { available_usd: 34.999336 },
  snackbars: [],
}));

vi.mock(
  "ag-grid-react",
  async () => await import("../../providers/__tests__/grid-mock"),
);
vi.mock("react-apexcharts", () => ({ default: () => null }));

vi.mock("../hooks/useApiKeys", () => ({
  useApiKeys: () => ({
    data: state.error ? undefined : state.keys,
    isLoading: false,
    isError: !!state.error,
    error: state.error,
    refetch: state.refetch,
  }),
  useApiKeyDetail: (id) => ({
    data: state.keys.find((k) => k.id === id) || null,
    isLoading: false,
  }),
  useCreateApiKey: () => ({
    mutate: (payload, opts) => {
      state.create(payload);
      opts?.onSuccess?.(state.createResult);
    },
    isPending: false,
    isError: false,
    reset: vi.fn(),
  }),
  useUpdateApiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useRevokeApiKey: () => ({
    mutate: (id, opts) => {
      state.revoke(id);
      opts?.onSuccess?.(state.revokeResult);
    },
    isPending: false,
  }),
  useRotateApiKey: () => ({
    mutate: (key, opts) => {
      state.rotate(key);
      opts?.onSuccess?.(state.rotateResult);
    },
    isPending: false,
    reset: vi.fn(),
  }),
  useSyncApiKeys: () => ({
    mutate: (id, opts) => {
      state.sync(id);
      opts?.onSuccess?.({ synced: 2 });
    },
    isPending: false,
  }),
}));

vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: {
      providers: {
        openai: { models: ["gpt-4o", "gpt-4o-mini"] },
        "flaky-upstream": { models: ["gpt-4o", "o1-mini"] },
      },
    },
  }),
}));

vi.mock("../../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: { providers: {} } }),
}));
vi.mock("../../models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => ({ data: { available: true } }),
  useManagedCatalog: () => ({ data: { models: [{ provider: "openrouter" }] } }),
}));

vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined, isError: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: undefined, isError: false }),
}));

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: { id: "default", baseUrl: "http://localhost:28490/v1" },
    isLoading: false,
  }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    role: state.role,
    user: { id: state.userId, organization_role: state.role },
  }),
}));
// The key writes read the org level (useGatewayAdmin): Owner 15, Admin 8,
// Member 3, Viewer 1.
const LEVELS = { Owner: 15, Admin: 8, Member: 3, Viewer: 1 };
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: LEVELS[state.role] ?? null,
    currentOrganizationId: state.organizationId,
    isReady: state.organizationReady,
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: ({ enabled } = {}) => ({
    credits: enabled ? state.credits : null,
    creditsAvailable: enabled && !!state.credits,
    visible: enabled && !!state.credits,
    isLoading: false,
  }),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) =>
    state.snackbars.push({ message, variant: options?.variant }),
}));

const KEYS = [
  {
    id: "k1",
    name: "support-bot",
    owner: "team-support",
    status: "active",
    keyPrefix: "sk-agentcc-4...",
    gatewayKeyId: "gw-k1",
    allowedModels: [],
    allowedProviders: [],
    metadata: { org_id: "org-1" },
    created_at: "2026-10-20T08:00:00Z",
    last_used_at: "2026-10-20T09:00:00Z",
    expires_at: null,
  },
  {
    id: "k2",
    name: "checkout-service",
    owner: "",
    status: "active",
    keyPrefix: "sk-agentcc-5...",
    gatewayKeyId: "gw-k2",
    allowedModels: ["gpt-4o", "gpt-4o-mini"],
    allowedProviders: ["openai"],
    metadata: {},
    created_at: "2026-10-19T08:00:00Z",
    last_used_at: null,
    expires_at: "2027-01-15T12:00:00Z",
  },
  {
    id: "k3",
    name: "old-ci",
    owner: "",
    status: "revoked",
    keyPrefix: "sk-agentcc-9...",
    gatewayKeyId: "gw-k3",
    allowedModels: [],
    allowedProviders: [],
    metadata: {},
    created_at: "2026-09-01T08:00:00Z",
    last_used_at: null,
    expires_at: null,
  },
];

function Where() {
  const location = useLocation();
  return <div data-testid="where">{location.pathname + location.search}</div>;
}

function renderPage(path = "/dashboard/gateway/keys") {
  const page = () => (
    <HelmetProvider>
      <ThemeProvider theme={faTestTheme("light")}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route
              path="/dashboard/gateway/keys"
              element={
                <>
                  <KeyManagementSection />
                  <Where />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>
  );
  const view = render(page());
  return { ...view, refresh: () => view.rerender(page()) };
}

const rowFor = (name) =>
  screen.getAllByTestId("ag-row").find((row) => within(row).queryByText(name));

beforeEach(() => {
  state.role = "Owner";
  state.organizationId = "org-a";
  state.organizationReady = true;
  state.userId = "signed-in-user";
  state.keys = KEYS.map((key) => ({
    ...key,
    user: "peer-user",
    organization: "org-a",
  }));
  state.error = null;
  state.refetch.mockReset();
  state.create.mockReset();
  state.createResult = {
    id: "k-new",
    name: "ci",
    key: "sk-agentcc-SECRET-ONCE",
  };
  state.rotate.mockReset();
  state.rotateResult = {
    secret: "sk-agentcc-ROTATED",
    created: { id: "k-rot", name: "support-bot" },
    expiryCopied: true,
    oldRevoked: true,
    gatewayRevoked: true,
    revokeWarning: null,
  };
  state.revoke.mockReset();
  state.revokeResult = { status: "revoked" };
  state.sync.mockReset();
  state.credits = { available_usd: 34.999336 };
  state.snackbars.length = 0;
});

describe("API keys page", () => {
  it("is one FA page: an 18 px semibold title over one key list", () => {
    renderPage();
    const title = screen.getByRole("heading", { level: 1, name: "API keys" });
    expect(window.getComputedStyle(title).fontSize).toBe("0.9375rem");
    expect(window.getComputedStyle(title).fontWeight).toBe("600");
    expect(screen.getAllByTestId("ag-row")).toHaveLength(3);
    const checkout = rowFor("checkout-service");
    expect(within(checkout).getByText("sk-agentcc-5...")).toBeInTheDocument();
    expect(within(checkout).getByText("Active")).toBeInTheDocument();
    expect(
      within(checkout).getByText("gpt-4o, gpt-4o-mini"),
    ).toBeInTheDocument();
    expect(within(checkout).getByText("Jan 15, 2027")).toBeInTheDocument();
    const support = rowFor("support-bot");
    expect(within(support).getByText("All models")).toBeInTheDocument();
    expect(within(support).getByText("Never")).toBeInTheDocument();
    expect(
      within(rowFor("old-ci")).getByText("Revoked locally"),
    ).toBeInTheDocument();
  });

  it("filters by status and by search", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "Revoked" }));
    expect(screen.getAllByTestId("ag-row")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "All" }));
    await user.type(screen.getByRole("textbox", { name: /Search/ }), "check");
    expect(screen.getAllByTestId("ag-row")).toHaveLength(1);
    expect(rowFor("checkout-service")).toBeTruthy();
  });

  // Purple (color="primary") at the native 38 px, as on
  // Datasets, Keys, Members and Evals; the theme default draws it black.
  it("draws Create key as the Future AGI primary button at the native size", () => {
    renderPage();
    const cta = screen.getAllByRole("button", { name: "Create key" })[0];
    expect(cta).toHaveClass("MuiButton-containedPrimary");
    expect(cta).toHaveClass("MuiButton-sizeMedium");
  });

  it("shows the one empty state with Create key when there is none", () => {
    state.keys = [];
    renderPage();
    expect(screen.getByText("No API keys yet")).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "Create key" }).length,
    ).toBeGreaterThan(0);
  });

  it("says the keys could not be loaded, with Retry, when the read fails", async () => {
    const user = userEvent.setup();
    state.error = new Error("Network Error");
    renderPage();
    expect(
      screen.getByText("API keys could not be loaded"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(state.refetch).toHaveBeenCalled();
  });

  it("opens Create key from ?create=1 and drops the parameter on close", async () => {
    const user = userEvent.setup();
    renderPage("/dashboard/gateway/keys?create=1");
    const dialog = await screen.findByRole("dialog", {
      name: "Create API key",
    });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent(
        /^\/dashboard\/gateway\/keys$/,
      ),
    );
  });

  it("does not offer an ignored editable ownership label", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getAllByRole("button", { name: "Create key" })[0]);
    const dialog = await screen.findByRole("dialog", {
      name: "Create API key",
    });
    expect(within(dialog).queryByRole("textbox", { name: "Owner" })).toBeNull();
    expect(
      within(dialog).getByText(/belongs to your signed-in account/i),
    ).toBeVisible();
  });

  it.each(Object.keys(LEVELS))(
    "%s creates a personal key and sees its secret once",
    async (role) => {
      state.role = role;
      const user = userEvent.setup();
      const page = renderPage();
      await user.click(
        screen.getAllByRole("button", { name: "Create key" })[0],
      );
      const dialog = await screen.findByRole("dialog", {
        name: "Create API key",
      });
      await user.type(within(dialog).getByLabelText(/^Name/), "ci");
      await user.click(within(dialog).getByRole("button", { name: "Create" }));
      expect(state.create).toHaveBeenCalledWith(
        expect.objectContaining({ gateway_id: "default", name: "ci" }),
      );
      expect(state.create.mock.calls[0][0]).not.toHaveProperty("owner");
      const reveal = await screen.findByRole("dialog", {
        name: "Copy your new key",
      });
      expect(
        within(reveal).getByText("sk-agentcc-SECRET-ONCE"),
      ).toBeInTheDocument();
      expect(
        within(reveal).getByText("http://localhost:28490/v1"),
      ).toBeInTheDocument();
      expect(
        within(reveal).getByText(/will not be shown again/i),
      ).toBeInTheDocument();
      // The secret is what is shown once, not the key's name.
      expect(
        within(reveal).getByText(
          (_, el) =>
            el?.tagName === "P" &&
            /^This is the only time the secret for ci is shown\./.test(
              el.textContent,
            ),
        ),
      ).toBeInTheDocument();

      await user.click(
        within(reveal).getByRole("button", { name: /Copy key/ }),
      );
      // user-event's clipboard stub stands in for the browser's.
      await waitFor(async () =>
        expect(await navigator.clipboard.readText()).toBe(
          "sk-agentcc-SECRET-ONCE",
        ),
      );

      // Escape does not throw the only copy of the key away.
      await user.keyboard("{Escape}");
      expect(
        screen.getByRole("dialog", { name: "Copy your new key" }),
      ).toBeInTheDocument();
      await user.click(within(reveal).getByRole("button", { name: "Done" }));
      await waitFor(() =>
        expect(screen.queryByText("sk-agentcc-SECRET-ONCE")).toBeNull(),
      );
      // A refetched server row carries canonical owner/org IDs, never the secret.
      state.keys = [
        ...state.keys,
        {
          id: "k-new",
          name: "ci",
          status: "active",
          user: state.userId,
          organization: state.organizationId,
        },
      ];
      page.refresh();
      await user.click(
        await within(rowFor("ci")).findByRole("button", {
          name: "Actions for ci",
        }),
      );
      await user.click(screen.getByRole("menuitem", { name: "Revoke" }));
      const confirm = await screen.findByRole("dialog", { name: "Revoke ci?" });
      await user.click(
        within(confirm).getByRole("button", { name: "Revoke key" }),
      );
      expect(state.revoke).toHaveBeenCalledExactlyOnceWith("k-new");
    },
  );

  it("offers the org's providers and their models as the key's access lists", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getAllByRole("button", { name: "Create key" })[0]);
    const dialog = await screen.findByRole("dialog", {
      name: "Create API key",
    });
    await user.click(within(dialog).getByLabelText(/^Allowed providers/));
    expect(
      screen.getByRole("option", { name: "flaky-upstream" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "openai" })).toBeInTheDocument();
  });

  it("rotates its own key: says what happens, then shows the new key once", async () => {
    state.keys[0].user = state.userId;
    const user = userEvent.setup();
    renderPage();
    await user.click(
      within(rowFor("support-bot")).getByRole("button", {
        name: /Actions for support-bot/,
      }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Rotate" }));
    const confirm = await screen.findByRole("dialog", {
      name: "Rotate support-bot?",
    });
    expect(
      within(confirm).getByText(/revoke the old key before showing the new key/i),
    ).toBeInTheDocument();
    await user.click(
      within(confirm).getByRole("button", { name: "Rotate key" }),
    );
    expect(state.rotate).toHaveBeenCalledWith(
      expect.objectContaining({ id: "k1" }),
    );
    const reveal = await screen.findByRole("dialog", {
      name: "Copy your new key",
    });
    expect(within(reveal).getByText("sk-agentcc-ROTATED")).toBeInTheDocument();
    expect(
      within(reveal).getByText(/support-bot's old key is revoked/i),
    ).toBeInTheDocument();
  });

  it("keeps the new key on screen and does not infer old-key activity from an unconfirmed revoke", async () => {
    state.keys[0].user = state.userId;
    const user = userEvent.setup();
    state.rotateResult = { ...state.rotateResult, oldRevoked: false };
    renderPage();
    await user.click(
      within(rowFor("support-bot")).getByRole("button", {
        name: /Actions for support-bot/,
      }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Rotate" }));
    await user.click(
      within(
        await screen.findByRole("dialog", { name: "Rotate support-bot?" }),
      ).getByRole("button", { name: "Rotate key" }),
    );
    const reveal = await screen.findByRole("dialog", {
      name: "Copy your new key",
    });
    expect(within(reveal).getByText("sk-agentcc-ROTATED")).toBeInTheDocument();
    expect(
      within(reveal).getByText(/Revocation of the old key could not be confirmed/i),
    ).toBeInTheDocument();
  });

  it("revokes a key from its row after a confirmation", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(
      within(rowFor("checkout-service")).getByRole("button", {
        name: /Actions for checkout-service/,
      }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Revoke" }));
    const confirm = await screen.findByRole("dialog", {
      name: "Revoke checkout-service?",
    });
    await user.click(
      within(confirm).getByRole("button", { name: "Revoke key" }),
    );
    expect(state.revoke).toHaveBeenCalledWith("k2");
    expect(state.snackbars).toEqual([
      { message: 'Key "checkout-service" revoked', variant: "success" },
    ]);
  });

  it("offers no row actions on a revoked key", () => {
    renderPage();
    expect(
      within(rowFor("old-ci")).queryByRole("button", { name: /Actions for/ }),
    ).toBeNull();
  });

  // A member sees the key writes Django refuses them disabled,
  // with "Owner or Admin only"; reading stays open.
  it("shows a member the keys with the controls Django refuses them disabled", () => {
    state.role = "Member";
    renderPage();
    expect(screen.getAllByTestId("ag-row")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Create key" })).toBeEnabled();
    expectAdminOnly(screen.getByRole("button", { name: /Sync/ }));
    screen
      .getAllByRole("button", { name: /Actions for/ })
      .forEach((actions) => expectAdminOnly(actions));
    expect(screen.queryByRole("link", { name: /Credits/ })).toBeNull();
  });

  it.each(Object.keys(LEVELS))(
    "%s opens personal creation from the deep link without admin configuration access",
    async (role) => {
      state.role = role;
      renderPage("/dashboard/gateway/keys?create=1");
      expect(
        await screen.findByRole("dialog", { name: "Create API key" }),
      ).toBeVisible();
      expect(screen.getByTestId("where")).toHaveTextContent("?create=1");
      expect(state.create).not.toHaveBeenCalled();
    },
  );

  it.each([
    ["Unknown", "org-a", true],
    ["Member", null, true],
    ["Member", "org-a", false],
  ])(
    "does not open for unavailable membership %s/%s/%s",
    async (role, org, ready) => {
      state.role = role;
      state.organizationId = org;
      state.organizationReady = ready;
      renderPage("/dashboard/gateway/keys?create=1");
      expect(
        screen.queryByRole("dialog", { name: "Create API key" }),
      ).toBeNull();
      expect(screen.getByRole("button", { name: "Create key" })).toBeDisabled();
      expect(state.create).not.toHaveBeenCalled();
      if (ready) {
        await waitFor(() =>
          expect(screen.getByTestId("where")).toHaveTextContent(
            /^\/dashboard\/gateway\/keys$/,
          ),
        );
      } else {
        expect(screen.getByTestId("where")).toHaveTextContent("?create=1");
      }
    },
  );

  it("shows the credit balance, linked to central Billing", () => {
    renderPage();
    expect(
      screen.getByRole("link", { name: /Credits \$34\.99/ }),
    ).toHaveAttribute("href", "/dashboard/settings/billing#ai-gateway-credits");
  });

  it("syncs keys from the gateway on request", async () => {
    const user = userEvent.setup();
    renderPage();
    await act(async () => {
      await user.click(
        screen.getByRole("button", { name: /Sync from gateway/ }),
      );
    });
    expect(state.sync).toHaveBeenCalledWith("default");
  });
});

describe.each(["Member", "Viewer"])("%s personal-key lifecycle", (role) => {
  it.each(["row", "drawer"])(
    "revokes its own key from the %s without edit/sync authority",
    async (entry) => {
      state.role = role;
      state.keys[0].user = state.userId;
      // Editable contact labels are deliberately unrelated to ownership.
      state.keys[0].owner = "Someone else's contact label";
      const user = userEvent.setup();
      renderPage();
      expectAdminOnly(screen.getByRole("button", { name: /Sync/ }));
      if (entry === "row") {
        const actions = within(rowFor("support-bot")).getByRole("button", {
          name: /Actions for/,
        });
        expect(actions).toBeEnabled();
        await user.click(actions);
        expect(screen.getByRole("menuitem", { name: "Edit" })).toHaveAttribute(
          "aria-disabled",
          "true",
        );
        expect(
          screen.getByRole("menuitem", { name: "Rotate" }),
        ).toHaveAttribute("aria-disabled", "true");
        await user.click(screen.getByRole("menuitem", { name: "Revoke" }));
      } else {
        await user.click(
          screen.getByRole("button", { name: "View details for support-bot" }),
        );
        expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
        expect(screen.getByRole("button", { name: "Rotate" })).toBeDisabled();
        expect(screen.getByRole("button", { name: "Revoke" })).toBeEnabled();
        await user.click(screen.getByRole("button", { name: "Revoke" }));
      }
      const confirm = await screen.findByRole("dialog", {
        name: "Revoke support-bot?",
      });
      await user.click(
        within(confirm).getByRole("button", { name: "Revoke key" }),
      );
      expect(state.revoke).toHaveBeenCalledExactlyOnceWith("k1");
      expect(state.sync).not.toHaveBeenCalled();
      expect(state.rotate).not.toHaveBeenCalled();
    },
  );

  it.each(["peer", "unknown", "foreign-org"])(
    "refuses %s ownership even when the contact label matches",
    async (kind) => {
      state.role = role;
      state.keys[0].owner = state.userId;
      if (kind === "unknown") state.keys[0].user = null;
      if (kind === "foreign-org") {
        state.keys[0].user = state.userId;
        state.keys[0].organization = "other-org";
      }
      const user = userEvent.setup();
      renderPage();
      expect(
        within(rowFor("support-bot")).getByRole("button", {
          name: /Actions for/,
        }),
      ).toBeDisabled();
      await user.click(
        screen.getByRole("button", { name: "View details for support-bot" }),
      );
      expect(screen.getByRole("button", { name: "Revoke" })).toBeDisabled();
      expect(state.revoke).not.toHaveBeenCalled();
    },
  );
});

it.each([
  "identity",
  "role",
  "organization",
  "membership",
  "ownership",
  "status",
])("rechecks %s at revoke confirmation", async (change) => {
  state.role = "Member";
  state.keys[0].user = state.userId;
  const user = userEvent.setup();
  const page = renderPage();
  await user.click(
    within(rowFor("support-bot")).getByRole("button", { name: /Actions for/ }),
  );
  await user.click(screen.getByRole("menuitem", { name: "Revoke" }));
  const confirm = await screen.findByRole("dialog", {
    name: "Revoke support-bot?",
  });
  if (change === "identity") state.userId = "new-signed-in-user";
  if (change === "role") state.role = "Unknown";
  if (change === "organization") state.organizationId = "other-org";
  if (change === "membership") state.organizationReady = false;
  if (change === "ownership")
    state.keys = state.keys.map((key) => ({ ...key, user: "peer-user" }));
  if (change === "status")
    state.keys = state.keys.map((key) => ({ ...key, status: "revoked" }));
  page.refresh();
  // Invoke the actual confirmation handler with the current identity/role.
  await user.click(within(confirm).getByRole("button", { name: "Revoke key" }));
  expect(state.revoke).not.toHaveBeenCalled();
});

// The grid cut the two-ended prefix
// to "sk-agentcc-d1e1…", so the tail that tells two keys apart showed only
// in the drawer. The Key column holds the whole prefix at its narrowest,
// and the grid still fits at 1440 px.
describe("API keys grid widths", () => {
  it("shows the whole two-ended prefix, head and tail, as the drawer does", async () => {
    state.keys = [{ ...KEYS[0], keyPrefix: "sk-agentcc-d1e1…3fb2" }];
    renderPage();
    expect(await screen.findByText("sk-agentcc-d1e1…3fb2")).toBeInTheDocument();
    const key = lastGrid.props.columnDefs.find((c) => c.colId === "key");
    expect(key.minWidth).toBeGreaterThanOrEqual(
      CELL_TEXT_PX["sk-agentcc-4f2a…9c1d"] + CELL_PADDING_PX,
    );
  });

  it("still fits its columns at 1440 px without scrolling sideways", async () => {
    renderPage();
    await screen.findByText("support-bot");
    expect(minimumGridWidth(lastGrid.props)).toBeLessThanOrEqual(
      gridBudget1440(),
    );
  });
});
