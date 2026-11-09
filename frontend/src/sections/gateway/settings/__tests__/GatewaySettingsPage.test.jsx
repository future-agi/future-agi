import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import GatewaySettingsSection from "../GatewaySettingsSection";
import { maskSecrets, MASK } from "../maskSecrets";

const state = vi.hoisted(() => ({
  tab: undefined,
  query: "",
  config: null,
  configError: null,
  role: "Owner",
  healthResult: null,
  navigate: null,
  credits: null,
  billingVisible: true,
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => state.navigate,
    useParams: () => ({ tab: state.tab }),
    useSearchParams: () => [new URLSearchParams(state.query), vi.fn()],
  };
});

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// The org level behind the Owner or Admin checks (useGatewayAdmin).
const LEVELS = { Owner: 15, Admin: 8, Member: 3, Viewer: 1 };
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: LEVELS[state.role] }),
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));

// Central billing's own hook: what the credit ledger answers.
vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: ({ enabled = true } = {}) => ({
    credits: enabled ? state.credits : null,
    creditsAvailable: enabled && Boolean(state.credits),
    visible: state.billingVisible,
    isLoading: false,
  }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: state.role },
    role: state.role,
  }),
}));

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual("@tanstack/react-query");
  return {
    ...actual,
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
    useMutation: (options) => ({
      isPending: false,
      mutate: (_variables, callbacks) => {
        const result = state.healthResult;
        if (callbacks?.onSuccess) callbacks.onSuccess(result);
        else if (options?.onSuccess) options.onSuccess(result);
      },
    }),
  };
});

vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: state.config,
    isLoading: false,
    error: state.configError,
    refetch: vi.fn(),
  }),
  useReloadConfig: () => ({ isPending: false, mutate: vi.fn() }),
}));

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    isLoading: false,
    gateway: {
      id: "default",
      name: "Agent Command Center Gateway",
      baseUrl: "http://localhost:28490/v1",
      status: "healthy",
      providerCount: 2,
      modelCount: 7,
      lastHealthCheck: null,
    },
  }),
}));

vi.mock("../OrgConfigSection", () => ({
  default: () => <div>Org config section</div>,
}));
vi.mock("../EmailAlertsCard", () => ({
  default: () => <div>Email alerts card</div>,
}));
vi.mock("../hooks/useBatchJobs", () => ({
  useSubmitBatch: () => ({ mutate: vi.fn(), isPending: false }),
  useBatchStatus: () => ({ data: null, isLoading: false }),
  useCancelBatch: () => ({ mutate: vi.fn(), isPending: false }),
}));

const STORED = {
  version: 27,
  server: null,
  logging: null,
  batch: null,
  cache: {
    enabled: true,
    redis: { address: "redis:6379", password: "hunter2" },
  },
  audit: { sinks: [{ type: "webhook", headers: { "X-Token": "abc123" } }] },
  mcp: { servers: { crm: { auth: { token: "mcp-bearer-xyz" } } } },
  providers: [{ name: "openai", api_key: "sk-live-123" }],
};

function renderPage() {
  return render(
    <HelmetProvider>
      <GatewaySettingsSection />
    </HelmetProvider>,
  );
}

describe("Gateway settings page", () => {
  beforeEach(() => {
    state.tab = undefined;
    state.query = "";
    state.config = STORED;
    state.configError = null;
    state.role = "Owner";
    state.healthResult = null;
    state.navigate = vi.fn();
    state.credits = null;
    state.billingVisible = true;
  });

  // Gateway credits are not served by this API (shell/page-status.js
  // HIDDEN_GATEWAY_CONTROLS.managedCredits): General shows no Billing &
  // credits panel to anyone, so it promises no ledger, statement or top-up.
  it.each(["Owner", "Admin", "Member"])(
    "shows %s no Billing & credits panel while gateway credits are hidden",
    (role) => {
      state.role = role;
      state.credits = { available_usd: 34.99, billing_available: true };
      renderPage();
      expect(
        screen.queryByRole("region", { name: "Billing & credits" }),
      ).toBeNull();
      expect(screen.queryByText(/top-up/i)).toBeNull();
    },
  );

  it("is a gateway page: an 18 px title and FA tabs, Batch jobs among them", () => {
    renderPage();
    expect(
      screen.getByRole("heading", { level: 1, name: "Gateway settings" }),
    ).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabs).toEqual([
      "General",
      "Org config",
      "Batch jobs",
      "Full config",
    ]);
  });

  // Batch jobs run as the organization. The tab opens from its URL with its
  // Submit Batch Job button (an Owner here).
  it("opens Batch jobs from ?tab=batch-jobs", () => {
    state.query = "tab=batch-jobs";
    renderPage();
    expect(screen.getByRole("tab", { name: "Batch jobs" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.getByRole("button", { name: "Submit Batch Job" }),
    ).toBeInTheDocument();
  });

  it.each(["Owner", "Member"])(
    "uses factual batch empty-state copy for %s",
    (role) => {
      state.role = role;
      state.query = "tab=batch-jobs";
      renderPage();
      expect(
        screen.getByText(
          role === "Owner"
            ? 'No batch jobs submitted. Click "Submit Batch Job" to get started.'
            : "No batch jobs submitted. An Owner or Admin can submit one.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(/No batch jobs submitted yet/)).toBeNull();
    },
  );

  it.each([[[]], [["cache"]], [["guardrails"]]])(
    "reports an enabled guardrail pipeline with sections %j",
    (sections) => {
      state.config = {
        ...STORED,
        cache: { enabled: false },
        org_sections_enforced: sections,
        guardrail_pipeline: { enabled: true },
      };
      renderPage();
      const value = screen.getByText("Org Sections Enforced").nextElementSibling
        .textContent;
      expect(value).toContain("guardrails");
      expect(value).not.toContain("cache");
    },
  );

  it("moves an old /settings/batch-jobs link to ?tab=batch-jobs", () => {
    state.tab = "batch-jobs";
    renderPage();
    expect(state.navigate).toHaveBeenCalledWith(
      "/dashboard/gateway/settings?tab=batch-jobs",
      { replace: true },
    );
  });

  it("switches tabs with ?tab=, never a /settings/<tab> path (that path flips the sidebar to the Future AGI settings nav)", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("tab", { name: "Org config" }));
    expect(state.navigate).toHaveBeenLastCalledWith(
      "/dashboard/gateway/settings?tab=org-config",
      { replace: true },
    );
  });

  it("goes back to General on the page's own path", async () => {
    state.query = "tab=org-config";
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("tab", { name: "General" }));
    expect(state.navigate).toHaveBeenLastCalledWith(
      "/dashboard/gateway/settings",
      { replace: true },
    );
  });

  it("reads the tab from ?tab=", () => {
    state.query = "tab=org-config";
    renderPage();
    expect(screen.getByRole("tab", { name: "Org config" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("Org config section")).toBeInTheDocument();
  });

  it("moves an old /settings/org-config link to ?tab=org-config", () => {
    state.tab = "org-config";
    renderPage();
    expect(state.navigate).toHaveBeenCalledWith(
      "/dashboard/gateway/settings?tab=org-config",
      { replace: true },
    );
  });

  // Every value shown is the gateway's own: a server or logging value the
  // gateway does not report is said to be unreported, never filled in.
  it("never claims server or logging values the gateway does not report", () => {
    renderPage();
    const body = document.body.textContent;
    ["Request Logging", "Include Bodies", "Disabled", "Host", "Port"].forEach(
      (label) => expect(screen.queryByText(label)).toBeNull(),
    );
    expect(body).not.toMatch(/Never/);
    expect(
      screen.getByText("This gateway did not report its server settings."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("This gateway did not report its logging settings."),
    ).toBeInTheDocument();
  });

  // The API still names the gateway by its former product name; the page
  // shows the product's name, AI Gateway.
  it("shows the gateway's identity with its endpoint, named AI Gateway", () => {
    renderPage();
    expect(screen.getByText("http://localhost:28490/v1")).toBeInTheDocument();
    expect(screen.getByText("AI Gateway")).toBeInTheDocument();
    expect(screen.queryByText(/Agent Command Center/)).toBeNull();
  });

  // Health Check and Reload Config post to the gateway: the console offers
  // them to an organization Owner or Admin, as on Providers and Budgets.
  it.each(["Member", "Viewer"])(
    "shows a %s Health Check and Reload Config disabled, with the role they need",
    (role) => {
      state.role = role;
      renderPage();
      for (const name of ["Health Check", "Reload Config"]) {
        const button = screen.getByRole("button", { name });
        expect(button).toBeDisabled();
        expect(
          button.closest('[aria-label="Owner or Admin only"]'),
        ).not.toBeNull();
      }
    },
  );

  it.each(["Owner", "Admin"])(
    "lets an %s run a health check and reload the config",
    (role) => {
      state.role = role;
      renderPage();
      for (const name of ["Health Check", "Reload Config"]) {
        const button = screen.getByRole("button", { name });
        expect(button).toBeEnabled();
        expect(button.closest('[aria-label="Owner or Admin only"]')).toBeNull();
      }
    },
  );

  it("shows the health check's answer once it ran, not a stored time it never had", async () => {
    state.healthResult = { status: "healthy", provider_count: 2 };
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: /health check/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/Gateway answered: healthy/i),
      ).toBeInTheDocument(),
    );
  });

  // Alerts are set on the Alerts page (rules and channels), the one
  // alerting surface: General has no second, email-only one.
  it("has no Email alerts section on General", () => {
    renderPage();
    expect(screen.queryByText("Email alerts card")).toBeNull();
  });

  it("masks every stored secret in Full config", () => {
    state.query = "tab=full-config";
    renderPage();
    const view = screen.getByTestId("full-config-json");
    const text = view.textContent;
    ["hunter2", "abc123", "mcp-bearer-xyz", "sk-live-123"].forEach((secret) =>
      expect(text).not.toContain(secret),
    );
    expect(text).toContain(MASK);
    expect(text).toContain("redis:6379");
  });

  // The config read is open to every member and masks stored org-config
  // secrets itself; the page masks the rest.
  it("shows a member Full config with every stored secret masked", () => {
    state.query = "tab=full-config";
    state.role = "Member";
    state.config = {
      ...STORED,
      cache: {
        enabled: true,
        redis: { password: "__encrypted__stored-fingerprint" },
      },
    };
    renderPage();
    const text = screen.getByTestId("full-config-json").textContent;
    expect(text).not.toContain("__encrypted__");
    ["abc123", "mcp-bearer-xyz", "sk-live-123"].forEach((secret) =>
      expect(text).not.toContain(secret),
    );
    expect(text).toContain(MASK);
  });

  it("shows one error state with a retry when the config cannot be read", () => {
    state.config = null;
    state.configError = new Error("boom");
    renderPage();
    expect(
      screen.getByText(/couldn't load this gateway's settings/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("keeps the gateway config error off Org config, which reads its own endpoint", () => {
    state.config = null;
    state.configError = new Error("boom");
    state.query = "tab=org-config";
    renderPage();
    expect(screen.getByText("Org config section")).toBeInTheDocument();
    expect(
      screen.queryByText(/couldn't load this gateway's settings/i),
    ).not.toBeInTheDocument();
  });

  it("shows the gateway config error on Full config too", () => {
    state.config = null;
    state.configError = new Error("boom");
    state.query = "tab=full-config";
    renderPage();
    expect(
      screen.getByText(/couldn't load this gateway's settings/i),
    ).toBeInTheDocument();
  });
});

describe("maskSecrets", () => {
  it("masks secret-looking keys and every header value, keeps the rest", () => {
    const out = maskSecrets({
      a: { password: "p", api_key: "k", sas_token: "s", name: "n" },
      headers: { Authorization: "Bearer x" },
      list: [{ secret_access_key: "z", region: "us" }],
      empty: { password: "" },
    });
    expect(out.a).toEqual({
      password: MASK,
      api_key: MASK,
      sas_token: MASK,
      name: "n",
    });
    expect(out.headers.Authorization).toBe(MASK);
    expect(out.list[0]).toEqual({ secret_access_key: MASK, region: "us" });
    expect(out.empty.password).toBe("");
  });

  it("leaves token counts and key ids readable", () => {
    const out = maskSecrets({
      max_tokens: 4096,
      per_key_rpm: 600,
      api_key_id: "key_1",
      secretAccessKey: "AKIA",
      "connection-string": "Endpoint=sb://x",
    });
    expect(out).toEqual({
      max_tokens: 4096,
      per_key_rpm: 600,
      api_key_id: "key_1",
      secretAccessKey: MASK,
      "connection-string": MASK,
    });
  });
});

describe("maskSecrets: the stored shapes that carry credentials", () => {
  const STORED_SHAPES = {
    mcp: {
      servers: {
        github: {
          url: "https://mcp.example/x",
          auth: {
            type: "api_key",
            header: "X-API-Key",
            key: "stored-mcp-value-1",
          },
        },
      },
    },
    alerting: {
      channels: [
        {
          name: "slack",
          type: "slack",
          url: "https://hooks.slack.com/services/T000/B000/SECRET3",
        },
      ],
    },
    audit: {
      sinks: [
        { type: "webhook", url: "https://audit.example/ingest?token=SECRET4" },
      ],
    },
    cache: {
      backend: "redis",
      redis: { address: "redis:6379", url: "redis://:SECRET6@redis:6379/0" },
    },
    providers: [
      {
        name: "openai",
        base_url: "https://api.openai.com/v1",
        api_key_id: "key_1",
        max_tokens: 4096,
      },
      { name: "proxy", base_url: "https://proxy.example/v1?api_key=SECRET7" },
    ],
  };

  it("masks every value under an auth object except its type and header", () => {
    const { auth } = maskSecrets(STORED_SHAPES).mcp.servers.github;
    expect(auth).toEqual({ type: "api_key", header: "X-API-Key", key: MASK });
  });

  it("masks alert-channel and audit-sink URLs past their host", () => {
    const out = maskSecrets(STORED_SHAPES);
    expect(out.alerting.channels[0].url).toBe(
      `https://hooks.slack.com/${MASK}`,
    );
    expect(out.audit.sinks[0].url).toBe(`https://audit.example/${MASK}`);
  });

  it("masks credentials inside any URL: the password and token-like query values", () => {
    const out = maskSecrets(STORED_SHAPES);
    expect(out.cache.redis.url).toBe(`redis://:${MASK}@redis:6379/0`);
    expect(out.providers[1].base_url).toBe(
      `https://proxy.example/v1?api_key=${MASK}`,
    );
    expect(maskSecrets({ url: "https://user:pw@host.example/p" }).url).toBe(
      `https://user:${MASK}@host.example/p`,
    );
    expect(maskSecrets({ url: "https://ghp_tok@host.example/p" }).url).toBe(
      `https://${MASK}@host.example/p`,
    );
  });

  it("leaves no secret of these shapes anywhere in the output", () => {
    const text = JSON.stringify(maskSecrets(STORED_SHAPES));
    expect(text).not.toMatch(/SECRET\d/);
  });

  it("keeps what is not a secret readable: server URLs, hosts, key ids and counts", () => {
    const out = maskSecrets(STORED_SHAPES);
    expect(out.mcp.servers.github.url).toBe("https://mcp.example/x");
    expect(out.providers[0]).toEqual(STORED_SHAPES.providers[0]);
    expect(out.cache.redis.address).toBe("redis:6379");
    expect(out.alerting.channels[0]).toMatchObject({
      name: "slack",
      type: "slack",
    });
  });
});
