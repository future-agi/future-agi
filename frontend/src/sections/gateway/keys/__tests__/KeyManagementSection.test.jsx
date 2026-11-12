import React from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@mui/material/styles";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import KeyManagementSection from "../KeyManagementSection";
import { ROTATE_REVOCATION_COPY } from "../rotate";
import { PLACEHOLDER_REQUEST_NOTE } from "../../utils/snippetBaseUrl";
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
  providers: [],
  providerError: false,
  providerLoading: false,
  providerRefetch: vi.fn(),
  baseUrl: "http://localhost:28490/v1",
  hosted: true,
  modeConfirmed: true,
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
  useProviderHealth: () => ({
    data: { providers: state.providers },
    isError: state.providerError,
    isLoading: state.providerLoading,
    refetch: state.providerRefetch,
  }),
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
    gateway: { id: "default", baseUrl: state.baseUrl },
    hosted: state.hosted,
    deploymentModeConfirmed: state.modeConfirmed,
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
            <Route path="*" element={<Where />} />
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

afterEach(() => vi.restoreAllMocks());

// setupTests stubs window.matchMedia with vi.fn().mockImplementation(...), and
// the vi.restoreAllMocks() above takes that implementation away after the
// first test: from then on every useMediaQuery read on this page
// (useResponsive) got undefined. A plain function is not a mock, so the
// restore leaves it alone.
const originalMatchMedia = window.matchMedia;
beforeAll(() => {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
});
afterAll(() => {
  window.matchMedia = originalMatchMedia;
});

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
  state.providers = [];
  state.providerError = false;
  state.providerLoading = false;
  state.providerRefetch.mockReset();
  state.baseUrl = "http://localhost:28490/v1";
  state.hosted = true;
  state.modeConfirmed = true;
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
      within(rowFor("old-ci")).getByText("Revoked"),
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

  // The page header keeps "Create key"; the empty state's action has its own
  // name, so a screen reader and a strict-mode walk find one of each.
  it("names the empty state's action Create your first key, apart from the header's Create key", async () => {
    state.keys = [];
    const user = userEvent.setup();
    renderPage();
    expect(screen.getAllByRole("button", { name: "Create key" })).toHaveLength(
      1,
    );
    await user.click(
      screen.getByRole("button", { name: "Create your first key" }),
    );
    expect(
      await screen.findByRole("dialog", { name: "Create API key" }),
    ).toBeVisible();
  });

  it("offers no first-key action to someone who cannot create a key", () => {
    state.keys = [];
    state.role = "Unknown";
    renderPage();
    expect(screen.getByText("No API keys yet")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create your first key" }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Create key" })).toBeDisabled();
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
      // The gateway reports only a loopback default, which is no address a
      // reader could call: the Base URL row shows the marked placeholder,
      // with the note beside it, and the loopback address appears nowhere.
      expect(
        within(reveal).getByText("https://YOUR_GATEWAY_URL/v1", {
          selector: "code",
        }),
      ).toBeInTheDocument();
      expect(
        within(reveal).getByRole("button", { name: "Copy base URL" }),
      ).toBeInTheDocument();
      expect(
        within(reveal).getByText(PLACEHOLDER_REQUEST_NOTE),
      ).toBeInTheDocument();
      expect(reveal.textContent).not.toMatch(/localhost|127\.0\.0\.1/);
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
      within(reveal).getByText(ROTATE_REVOCATION_COPY.local("support-bot")),
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
describe("first request from a fresh key", () => {
  beforeEach(() => {
    state.baseUrl = "https://gateway.example.test/v1/";
    state.providers = [
      { name: "other", pushed: true, healthy: true, request_count: 20, models: ["model-a"] },
      { name: "team", pushed: true, healthy: true, request_count: 0, models: ["model-a", "model-b"] },
      { name: "draft", pushed: false, models: ["model-a"] },
    ];
    state.createResult = {
      id: "database-row-only", gatewayKeyId: "gateway/key & 1", name: "ci",
      key: "fresh-key-fixture-only", allowedModels: ["team/model-b"], allowedProviders: ["team"],
    };
  });

  async function createFresh(user) {
    await user.click(screen.getAllByRole("button", { name: "Create key" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "Create API key" });
    await user.type(within(dialog).getByLabelText(/^Name/), "ci");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    return screen.findByRole("dialog", { name: "Copy your new key" });
  }

  function expectNoRunnableRequest(reveal) {
    expect(within(reveal).queryByRole("button", { name: /Copy (cURL|Python|TypeScript) request/ })).toBeNull();
    expect(within(reveal).queryByRole("button", { name: "Copy setup commands" })).toBeNull();
    expect(within(reveal).queryByRole("tablist", { name: "Language" })).toBeNull();
  }

  it("copies the exact fresh key and allowed configured route, then forgets it", async () => {
    const user = userEvent.setup();
    const writes = vi.spyOn(localStorage, "setItem");
    const sessionWrites = vi.spyOn(sessionStorage, "setItem");
    const fetch = vi.spyOn(globalThis, "fetch");
    renderPage();
    const reveal = await createFresh(user);
    expectNoRunnableRequest(reveal);
    const select = within(reveal).getByRole("combobox", { name: "Model for first request" });
    expect(within(select).getAllByRole("option").map((option) => option.value)).toEqual(["", "team/model-b"]);
    await user.selectOptions(select, "team/model-b");
    expect(within(reveal).getByText(/health is unmeasured/i)).toBeVisible();
    expect(within(reveal).getByText(/chat-shaped example.*compatibility.*unverified/i)).toBeVisible();
    await user.click(within(reveal).getByRole("button", { name: "Copy cURL request" }));
    const copied = await navigator.clipboard.readText();
    expect(copied).toContain("export AGENTCC_API_KEY='fresh-key-fixture-only'");
    expect(copied).toContain("https://gateway.example.test/v1/chat/completions");
    expect(copied).toContain('"model": "team/model-b"');
    expect(copied).not.toMatch(/YOUR_|other\/|team\/model-a/);
    const logs = within(reveal).getByRole("link", { name: "View logs for this key" });
    expect(logs).toHaveAttribute("href", "/dashboard/gateway/logs?api_key_id=gateway%2Fkey%20%26%201");
    for (const link of within(reveal).queryAllByRole("link")) expect(link.href).not.toContain(state.createResult.key);
    expect(state.create).toHaveBeenCalledTimes(1);
    expect(fetch).not.toHaveBeenCalled();
    expect(JSON.stringify([...writes.mock.calls, ...sessionWrites.mock.calls])).not.toContain(state.createResult.key);
    await user.click(within(reveal).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByText(/export AGENTCC_API_KEY=/)).toBeNull());
    expect(screen.queryByText(state.createResult.key)).toBeNull();
  });

  it("switches SDK languages without changing the fresh quoted key or selected qualified route", async () => {
    state.createResult.key = "fresh-'quoted\"-$HOME";
    state.createResult.allowedModels = ["team/model-a", "team/model-b"];
    const exportCommand = "export AGENTCC_API_KEY='fresh-'\\''quoted\"-$HOME'";
    const user = userEvent.setup();
    const writes = vi.spyOn(Storage.prototype, "setItem");
    const fetch = vi.spyOn(globalThis, "fetch");
    renderPage();
    const reveal = await createFresh(user);
    const select = within(reveal).getByRole("combobox", { name: "Model for first request" });
    await user.selectOptions(select, "team/model-b");

    for (const recipe of [
      {
        language: "Python",
        install: "python -m pip install openai",
        environment: 'api_key=os.environ["AGENTCC_API_KEY"]',
        base: 'base_url="https://gateway.example.test/v1"',
        model: 'model="team/model-b"',
        run: /Save as first-request\.py, then run python first-request\.py/,
      },
      {
        language: "TypeScript",
        install: "npm install openai",
        environment: "apiKey: process.env.AGENTCC_API_KEY",
        base: 'baseURL: "https://gateway.example.test/v1"',
        model: 'model: "team/model-b"',
        run: /Save as first-request\.mts, then run node first-request\.mts/,
      },
    ]) {
      const tab = within(reveal).getByRole("tab", { name: recipe.language });
      await user.click(tab);
      expect(tab).toHaveAttribute("aria-selected", "true");
      expect(select).toHaveValue("team/model-b");
      const panel = within(reveal).getByRole("tabpanel", { name: recipe.language });
      const setup = `${recipe.install}\n${exportCommand}`;
      expect(within(panel).getByText(setup, { normalizer: (value) => value })).toBeVisible();
      expect(within(panel).getByText(recipe.run)).toBeVisible();
      if (recipe.language === "TypeScript") {
        expect(within(panel).getByText(/Node\.js 22\.18\+/)).toBeVisible();
      }
      await user.click(within(panel).getByRole("button", { name: "Copy setup commands" }));
      expect(await navigator.clipboard.readText()).toBe(setup);
      await user.click(within(panel).getByRole("button", { name: `Copy ${recipe.language} request` }));
      const copied = await navigator.clipboard.readText();
      expect(copied).toContain(recipe.environment);
      expect(copied).toContain(recipe.base);
      expect(copied).toContain(recipe.model);
      // Check the stable fixture marker as well as the raw key: JSON/string
      // serialization escapes its quote and must not hide accidental embedding.
      expect(copied).not.toContain("fresh-");
      expect(copied).not.toContain(state.createResult.key);
      expect(copied).not.toMatch(/export AGENTCC_API_KEY|YOUR_|other\/|team\/model-a|\/v1\/v1/);
      expect(within(reveal).getByText(/health is unmeasured/i)).toBeVisible();
      expect(within(reveal).getByText(/chat-shaped example.*compatibility.*unverified/i)).toBeVisible();
    }

    await user.click(within(reveal).getByRole("tab", { name: "cURL" }));
    await user.click(within(reveal).getByRole("button", { name: "Copy cURL request" }));
    const curl = await navigator.clipboard.readText();
    expect(curl).toContain(exportCommand);
    expect(curl).toContain('"model": "team/model-b"');
    expect(curl).toContain("https://gateway.example.test/v1/chat/completions");
    expect(within(reveal).queryByRole("button", { name: "Copy setup commands" })).toBeNull();
    expect(within(reveal).getByRole("link", { name: "View logs for this key" })).toHaveAttribute(
      "href", "/dashboard/gateway/logs?api_key_id=gateway%2Fkey%20%26%201",
    );
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/dashboard\/gateway\/keys$/);
    for (const link of within(reveal).getAllByRole("link")) {
      expect(decodeURIComponent(link.href)).not.toContain(state.createResult.key);
    }
    expect(writes.mock.calls.flat().join("\n")).not.toContain("fresh-");
    expect(state.create).toHaveBeenCalledTimes(1);
    expect(state.rotate).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["Done", "unmount"])("forgets SDK setup and the fresh secret on %s", async (exit) => {
    const user = userEvent.setup();
    const writes = vi.spyOn(Storage.prototype, "setItem");
    const page = renderPage();
    const reveal = await createFresh(user);
    await user.selectOptions(within(reveal).getByRole("combobox", { name: "Model for first request" }), "team/model-b");
    await user.click(within(reveal).getByRole("tab", { name: "Python" }));
    expect(within(reveal).getByText(/export AGENTCC_API_KEY=/)).toBeVisible();
    if (exit === "Done") {
      await user.click(within(reveal).getByRole("button", { name: "Done" }));
    } else {
      page.unmount();
    }
    await waitFor(() => expect(screen.queryByText(state.createResult.key)).toBeNull());
    expect(screen.queryByText(/export AGENTCC_API_KEY=/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy Python request" })).toBeNull();
    expect(writes.mock.calls.flat().join("\n")).not.toContain("fresh-");
    expect(state.create).toHaveBeenCalledTimes(1);
    expect(state.rotate).not.toHaveBeenCalled();
  });

  it("keeps the submitted restrictions when a legacy reply normalizes omitted access lists to empty", async () => {
    state.createResult.allowedModels = [];
    state.createResult.allowedProviders = [];
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getAllByRole("button", { name: "Create key" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "Create API key" });
    await user.type(within(dialog).getByLabelText(/^Name/), "ci");
    await user.type(within(dialog).getByRole("combobox", { name: "Allowed models" }), "team/model-b{Enter}");
    await user.type(within(dialog).getByRole("combobox", { name: "Allowed providers" }), "team{Enter}");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    expect(state.create).toHaveBeenCalledWith(expect.objectContaining({
      allowed_models: ["team/model-b"], allowed_providers: ["team"],
    }));
    const reveal = await screen.findByRole("dialog", { name: "Copy your new key" });
    const select = within(reveal).getByRole("combobox", { name: "Model for first request" });
    expect(within(select).getAllByRole("option").map((option) => option.value)).toEqual(["", "team/model-b"]);
    await user.selectOptions(select, "team/model-b");
    await user.click(within(reveal).getByRole("tab", { name: "TypeScript" }));
    await user.click(within(reveal).getByRole("button", { name: "Copy TypeScript request" }));
    expect(await navigator.clipboard.readText()).toContain('model: "team/model-b"');
    expect(state.create).toHaveBeenCalledTimes(1);
  });

  it("retains wire-format identifiers and closes the reveal when opening filtered logs", async () => {
    state.createResult = { ...state.createResult, gatewayKeyId: undefined, gateway_key_id: "wire-key" };
    const user = userEvent.setup();
    renderPage();
    const reveal = await createFresh(user);
    await user.selectOptions(within(reveal).getByRole("combobox", { name: "Model for first request" }), "team/model-b");
    await user.click(within(reveal).getByRole("tab", { name: "TypeScript" }));
    await user.click(within(reveal).getByRole("link", { name: "View logs for this key" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/dashboard/gateway/logs?api_key_id=wire-key");
    expect(screen.queryByText(state.createResult.key)).toBeNull();
    expect(state.create).toHaveBeenCalledTimes(1);
  });

  it("does not filter logs by a database identifier when the gateway identifier is absent", async () => {
    delete state.createResult.gatewayKeyId;
    const user = userEvent.setup();
    renderPage();
    const reveal = await createFresh(user);
    expect(within(reveal).getByRole("link", { name: "View logs" })).toHaveAttribute("href", "/dashboard/gateway/logs");
  });

  it.each([
    ["provider excluded", { allowedProviders: ["missing"] }],
    ["model excluded", { allowedModels: ["not-configured"] }],
    ["bare model is not a grant for the qualified request", { allowedModels: ["model-b"] }],
    ["malformed model restriction", { allowedModels: "team/model-b" }],
  ])("offers discovery, not a ready call, for %s", async (_, restriction) => {
    Object.assign(state.createResult, restriction);
    const user = userEvent.setup();
    renderPage();
    const reveal = await createFresh(user);
    expectNoRunnableRequest(reveal);
    expect(within(reveal).getByText(/No configured route matches this key's access lists/)).toBeVisible();
    expect(within(reveal).getByRole("link", { name: "Discover models" })).toHaveAttribute("href", "/dashboard/gateway/models");
    expect(state.create).toHaveBeenCalledTimes(1);
  });

  it("offers discovery rather than SDK setup when every allowed source is unpushed", async () => {
    state.providers = [{ name: "team", pushed: false, healthy: true, request_count: 20, models: ["model-b"] }];
    const user = userEvent.setup();
    renderPage();
    const reveal = await createFresh(user);
    expectNoRunnableRequest(reveal);
    expect(within(reveal).queryByRole("combobox", { name: "Model for first request" })).toBeNull();
    expect(within(reveal).getByRole("link", { name: "Discover models" })).toBeVisible();
    expect(state.create).toHaveBeenCalledTimes(1);
  });

  it.each(["missing URL", "unconfirmed deployment", "self-hosted", "localhost", "URL credentials"])(
    "offers setup when the endpoint cannot be established: %s", async (kind) => {
      if (kind === "missing URL") state.baseUrl = "";
      if (kind === "unconfirmed deployment") state.modeConfirmed = false;
      if (kind === "self-hosted") state.hosted = false;
      if (kind === "localhost") state.baseUrl = "http://localhost:3000/v1";
      if (kind === "URL credentials") state.baseUrl = "https://user:password@example.test/v1";
      const user = userEvent.setup();
      renderPage();
      const reveal = await createFresh(user);
      expectNoRunnableRequest(reveal);
      expect(within(reveal).getByRole("link", { name: "Open request setup" })).toHaveAttribute("href", "/dashboard/gateway");
      expect(within(reveal).getByText(/Copy your key before leaving/)).toBeVisible();
    },
  );

  it("retries only the failed provider read and never uses its cached routes", async () => {
    state.providerError = true;
    const user = userEvent.setup();
    const page = renderPage();
    const reveal = await createFresh(user);
    expect(within(reveal).getByText(/Configured models could not be loaded/)).toBeVisible();
    expect(within(reveal).queryByRole("combobox", { name: "Model for first request" })).toBeNull();
    expectNoRunnableRequest(reveal);
    await user.click(within(reveal).getByRole("button", { name: "Retry models" }));
    expect(state.providerRefetch).toHaveBeenCalledTimes(1);
    state.providerError = false;
    page.refresh();
    const select = within(reveal).getByRole("combobox", { name: "Model for first request" });
    await user.selectOptions(select, "team/model-b");
    await user.click(within(reveal).getByRole("tab", { name: "Python" }));
    expect(within(reveal).getByRole("button", { name: "Copy Python request" })).toBeEnabled();
    state.providerError = true;
    page.refresh();
    expectNoRunnableRequest(reveal);
    expect(within(reveal).getByRole("button", { name: "Retry models" })).toBeEnabled();
    expect(within(reveal).getByText(state.createResult.key)).toBeVisible();
    expect(state.create).toHaveBeenCalledTimes(1);
    expect(state.rotate).not.toHaveBeenCalled();
  });

  it("prefers a measured healthy allowed route and excludes unapplied or unhealthy routes", async () => {
    state.createResult.allowedModels = [];
    state.createResult.allowedProviders = [];
    state.providers.push({ name: "broken", pushed: true, healthy: false, models: ["model-a"] });
    const user = userEvent.setup();
    renderPage();
    const reveal = await createFresh(user);
    const select = within(reveal).getByRole("combobox", { name: "Model for first request" });
    expect(select).toHaveValue("other/model-a");
    expect(within(select).queryByRole("option", { name: "draft/model-a" })).toBeNull();
    expect(within(select).queryByRole("option", { name: "broken/model-a" })).toBeNull();
    expect(within(reveal).getByRole("button", { name: "Copy cURL request" })).toBeEnabled();
  });
});

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
