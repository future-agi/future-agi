import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, within } from "src/utils/test-utils";
import ProviderManagementSection from "./providers/ProviderManagementSection";
import ProviderConfigView from "./providers/ProviderConfigView";
import ExperimentsSection from "./experiments/ExperimentsSection";
import ExperimentDetailSection from "./experiments/ExperimentDetailSection";
import KeyManagementSection from "./keys/KeyManagementSection";
import KeyDetailDrawer from "./keys/KeyDetailDrawer";
import ConfigHistoryDrawer from "./settings/ConfigHistoryDrawer";
import ServersTab from "./mcp/servers/ServersTab";
import GuardrailsTab from "./mcp/guardrails/GuardrailsTab";
import PlaygroundTab from "./mcp/playground/PlaygroundTab";
import ManagedModelsCard from "./models/ManagedModelsCard";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "./testing/adminOnly";

// The gateway admin role check in the console: only an organization Owner
// or Admin may change the gateway config, its API keys and its shadow
// experiments, so every control that does so shows disabled with "Owner or
// Admin only" for a Member, a Viewer or a user whose level is not loaded.
// Reads stay open on every screen. AdminGatingOwned.test.jsx covers the
// Guardrails page and the Org Config editor.

let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({}),
  useNavigate: () => vi.fn(),
}));

vi.mock("react-apexcharts", () => ({ default: () => null }));

const mutation = () => ({
  mutate: vi.fn(),
  isPending: false,
  isError: false,
  error: null,
});

const PROVIDERS = {
  openai: { base_url: "https://api.openai.com/v1", models: ["gpt-4o"] },
};

const MCP_CONFIG = {
  providers: PROVIDERS,
  mcp: {
    enabled: true,
    servers: {
      docs: { url: "https://mcp.example.com/docs", transport: "http" },
    },
    guardrails: { enabled: true },
  },
};

vi.mock("./context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));

vi.mock("./providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: MCP_CONFIG, isLoading: false }),
  useProviderHealth: () => ({ data: { providers: [] }, isLoading: false }),
  useReloadConfig: () => mutation(),
  useRemoveProvider: () => mutation(),
  refreshGatewayConfig: vi.fn(),
}));

const VERSIONS = [
  { id: "v2", version: 2, is_active: true, created_at: "2026-10-20" },
  { id: "v1", version: 1, is_active: false, created_at: "2026-10-19" },
];

vi.mock("./providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null, isLoading: false }),
  useOrgConfigHistory: () => ({ data: VERSIONS, isLoading: false }),
  useActivateOrgConfig: () => mutation(),
}));
vi.mock("./settings/ConfigDiffView", () => ({ default: () => null }));

vi.mock("./providers/AddProviderDialog", () => ({ default: () => null }));
vi.mock("./providers/ProviderHealthTable", () => ({
  default: () => <div>Provider health table</div>,
}));

vi.mock("./mcp/hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: { enabled: true, servers: [{ server_id: "docs" }] },
    isLoading: false,
  }),
  useMCPTools: () => ({
    data: [{ name: "docs_search", server: "docs" }],
    isLoading: false,
    isError: false,
  }),
  useRemoveMCPServer: () => mutation(),
  useUpdateMCPServer: () => mutation(),
  useUpdateMCPGuardrails: () => mutation(),
  useTestMCPTool: () => mutation(),
}));
vi.mock("./mcp/servers/AddMCPServerDialog", () => ({ default: () => null }));
vi.mock("./mcp/servers/ToolsTable", () => ({ default: () => null }));

const EXPERIMENTS = [
  { id: "exp-a", name: "Active shadow", status: "active" },
  { id: "exp-p", name: "Paused shadow", status: "paused" },
  { id: "exp-c", name: "Completed shadow", status: "completed" },
];
let mockDetail = EXPERIMENTS[0];

vi.mock("./experiments/hooks/useExperiments", () => ({
  useExperiments: () => ({ data: EXPERIMENTS, isLoading: false, error: null }),
  useExperimentDetail: () => ({
    data: mockDetail,
    isLoading: false,
    error: null,
  }),
  useExperimentStats: () => ({ data: null, isLoading: false }),
  useShadowResults: () => ({ data: null, isLoading: false }),
  useCreateExperiment: () => mutation(),
  usePauseExperiment: () => mutation(),
  useResumeExperiment: () => mutation(),
  useCompleteExperiment: () => mutation(),
  useDeleteExperiment: () => mutation(),
}));
vi.mock("./experiments/CreateExperimentDialog", () => ({
  default: () => null,
}));

const KEY = {
  id: "key-1",
  name: "prod key",
  status: "active",
  keyPrefix: "sk-agcc-ab",
  gatewayKeyId: "gw-key-1",
  allowedModels: [],
  allowedProviders: [],
};
let mockKeys = [KEY];

vi.mock("./keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: mockKeys, isLoading: false, error: null }),
  useSyncApiKeys: () => mutation(),
  useApiKeyDetail: () => ({ data: KEY, isLoading: false }),
  useRevokeApiKey: () => mutation(),
  useUpdateApiKey: () => mutation(),
  useCreateApiKey: () => mutation(),
}));
vi.mock("./keys/CreateKeyDialog", () => ({ default: () => null }));
vi.mock("./analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined, isError: false }),
}));
vi.mock("./analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: undefined, isError: false }),
}));

vi.mock("./models/hooks/useManagedModels", () => ({
  useSetManagedModelsEnabled: () => mutation(),
  saveErrorMessage: (_err, fallback) => fallback,
}));

const MANAGED_STATUS = {
  available: true,
  enabled: false,
  markup_percent: 5,
  balance_usd: 10,
};

beforeEach(() => {
  mockOrgLevel = 8;
  mockDetail = EXPERIMENTS[0];
  mockKeys = [KEY];
});

describe("Providers", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s sees Add Provider disabled and Reload open",
    (_who, level) => {
      mockOrgLevel = level;
      render(<ProviderManagementSection />);
      expect(screen.getByText("Provider health table")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Add Provider" }));
      expect(
        screen.getByRole("button", { name: "Reload Config" }),
      ).toBeEnabled();
    },
  );

  it.each(ADMIN_LEVELS)("%s can add a provider", (_who, level) => {
    mockOrgLevel = level;
    render(<ProviderManagementSection />);
    expectEditable(screen.getByRole("button", { name: "Add Provider" }));
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads a provider with Edit and Remove disabled",
    (_who, level) => {
      mockOrgLevel = level;
      render(<ProviderConfigView config={{ providers: PROVIDERS }} />);
      expect(screen.getByText("https://api.openai.com/v1")).toBeInTheDocument();
      expectAdminOnly(
        screen.getByRole("button", { name: "Edit provider openai" }),
      );
      expectAdminOnly(
        screen.getByRole("button", { name: "Remove provider openai" }),
      );
    },
  );

  it.each(ADMIN_LEVELS)("%s can edit and remove a provider", (_who, level) => {
    mockOrgLevel = level;
    render(<ProviderConfigView config={{ providers: PROVIDERS }} />);
    expectEditable(
      screen.getByRole("button", { name: "Edit provider openai" }),
    );
    expectEditable(
      screen.getByRole("button", { name: "Remove provider openai" }),
    );
  });
});

describe("Shadow experiments", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s reads the list with New, Pause, Resume and Delete disabled",
    (_who, level) => {
      mockOrgLevel = level;
      render(<ExperimentsSection />);
      expect(screen.getByText("Active shadow")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "New Experiment" }));
      expectAdminOnly(screen.getByRole("button", { name: "Pause" }));
      expectAdminOnly(screen.getByRole("button", { name: "Resume" }));
      expectAdminOnly(screen.getByRole("button", { name: "Delete" }));
      screen
        .getAllByRole("button", { name: "View Details" })
        .forEach((view) => expect(view).toBeEnabled());
    },
  );

  it.each(ADMIN_LEVELS)(
    "%s can create, pause, resume and delete",
    (_who, level) => {
      mockOrgLevel = level;
      render(<ExperimentsSection />);
      expectEditable(screen.getByRole("button", { name: "New Experiment" }));
      expectEditable(screen.getByRole("button", { name: "Pause" }));
      expectEditable(screen.getByRole("button", { name: "Resume" }));
      expectEditable(screen.getByRole("button", { name: "Delete" }));
    },
  );

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads an experiment with Pause, Complete and Delete disabled",
    (_who, level) => {
      mockOrgLevel = level;
      render(<ExperimentDetailSection experimentId="exp-a" onBack={vi.fn()} />);
      expect(screen.getByText("Active shadow")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Pause" }));
      expectAdminOnly(screen.getByRole("button", { name: "Complete" }));
      expectAdminOnly(screen.getByRole("button", { name: "Delete" }));
    },
  );

  it.each(NOT_ADMIN_LEVELS)(
    "%s sees Resume disabled on a paused experiment",
    (_who, level) => {
      mockOrgLevel = level;
      mockDetail = EXPERIMENTS[1];
      render(<ExperimentDetailSection experimentId="exp-p" onBack={vi.fn()} />);
      expectAdminOnly(screen.getByRole("button", { name: "Resume" }));
    },
  );

  it.each(ADMIN_LEVELS)("%s can act on an experiment", (_who, level) => {
    mockOrgLevel = level;
    render(<ExperimentDetailSection experimentId="exp-a" onBack={vi.fn()} />);
    expectEditable(screen.getByRole("button", { name: "Pause" }));
    expectEditable(screen.getByRole("button", { name: "Complete" }));
    expectEditable(screen.getByRole("button", { name: "Delete" }));
  });
});

describe("API keys", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s reads the keys with Sync and Create Key disabled",
    (_who, level) => {
      mockOrgLevel = level;
      render(<KeyManagementSection />);
      expect(screen.getByText("prod key")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Sync" }));
      expectAdminOnly(screen.getByRole("button", { name: "Create Key" }));
    },
  );

  it.each(NOT_ADMIN_LEVELS)(
    "%s sees Create Your First Key disabled on an empty list",
    (_who, level) => {
      mockOrgLevel = level;
      mockKeys = [];
      render(<KeyManagementSection />);
      expectAdminOnly(
        screen.getByRole("button", { name: "Create Your First Key" }),
      );
    },
  );

  it.each(ADMIN_LEVELS)("%s can sync and create keys", (_who, level) => {
    mockOrgLevel = level;
    render(<KeyManagementSection />);
    expectEditable(screen.getByRole("button", { name: "Sync" }));
    expectEditable(screen.getByRole("button", { name: "Create Key" }));
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads a key with Edit and Revoke disabled and View Logs open",
    (_who, level) => {
      mockOrgLevel = level;
      render(
        <KeyDetailDrawer keyId="key-1" open onClose={vi.fn()} gatewayId="gw" />,
      );
      expect(screen.getByText("prod key")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Edit" }));
      expectAdminOnly(screen.getByRole("button", { name: "Revoke Key" }));
      expect(screen.getByRole("button", { name: "View Logs" })).toBeEnabled();
    },
  );

  it.each(ADMIN_LEVELS)("%s can edit and revoke a key", (_who, level) => {
    mockOrgLevel = level;
    render(
      <KeyDetailDrawer keyId="key-1" open onClose={vi.fn()} gatewayId="gw" />,
    );
    expectEditable(screen.getByRole("button", { name: "Edit" }));
    expectEditable(screen.getByRole("button", { name: "Revoke Key" }));
  });
});

// The drawer refreshes the gateway config after an activation, so it reads
// the query client.
const renderHistory = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ConfigHistoryDrawer open onClose={vi.fn()} />
    </QueryClientProvider>,
  );

describe("Config version history", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s can view a version but not activate one",
    (_who, level) => {
      mockOrgLevel = level;
      renderHistory();
      screen
        .getAllByRole("button", { name: "View" })
        .forEach((view) => expect(view).toBeEnabled());
      expectAdminOnly(screen.getByRole("button", { name: "Activate" }));
    },
  );

  it.each(ADMIN_LEVELS)("%s can activate a version", (_who, level) => {
    mockOrgLevel = level;
    renderHistory();
    expectEditable(screen.getByRole("button", { name: "Activate" }));
  });
});

describe("MCP", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s reads the servers with Add, Edit and Delete disabled",
    (_who, level) => {
      mockOrgLevel = level;
      render(<ServersTab gatewayId="default" />);
      expect(
        screen.getByText("https://mcp.example.com/docs"),
      ).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Add Server" }));
      expectAdminOnly(screen.getByRole("button", { name: "Edit" }));
      expectAdminOnly(screen.getByRole("button", { name: "Delete" }));
    },
  );

  it.each(ADMIN_LEVELS)(
    "%s can add, edit and delete a server",
    (_who, level) => {
      mockOrgLevel = level;
      render(<ServersTab gatewayId="default" />);
      expectEditable(screen.getByRole("button", { name: "Add Server" }));
      expectEditable(screen.getByRole("button", { name: "Edit" }));
      expectEditable(screen.getByRole("button", { name: "Delete" }));
    },
  );

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads MCP guardrails with Save disabled",
    (_who, level) => {
      mockOrgLevel = level;
      render(<GuardrailsTab gatewayId="default" />);
      expectAdminOnly(screen.getByRole("button", { name: "Save Guardrails" }));
    },
  );

  it.each(ADMIN_LEVELS)("%s can save MCP guardrails", (_who, level) => {
    mockOrgLevel = level;
    render(<GuardrailsTab gatewayId="default" />);
    expectEditable(screen.getByRole("button", { name: "Save Guardrails" }));
  });

  const pickTool = () => {
    const input = screen.getByPlaceholderText("Select a tool to test...");
    fireEvent.mouseDown(input);
    fireEvent.change(input, { target: { value: "docs" } });
    fireEvent.click(screen.getByRole("option", { name: "docs_search" }));
  };

  it.each(NOT_ADMIN_LEVELS)(
    "%s can pick a tool in the playground but not execute it",
    (_who, level) => {
      mockOrgLevel = level;
      render(<PlaygroundTab gatewayId="default" />);
      pickTool();
      expectAdminOnly(screen.getByRole("button", { name: "Execute" }));
    },
  );

  it.each(ADMIN_LEVELS)("%s can execute a tool", (_who, level) => {
    mockOrgLevel = level;
    render(<PlaygroundTab gatewayId="default" />);
    pickTool();
    expectEditable(screen.getByRole("button", { name: "Execute" }));
  });
});

describe("Managed models", () => {
  it("the switch says Owner or Admin only when the page cannot write", () => {
    render(
      <ManagedModelsCard
        status={MANAGED_STATUS}
        isLoading={false}
        error={null}
        onRetry={vi.fn()}
        gatewayId="default"
        canWrite={false}
      />,
    );
    const toggle = screen.getByRole("checkbox", { name: "Use managed models" });
    expect(toggle).toBeDisabled();
    expect(toggle.closest('[aria-label="Owner or Admin only"]')).not.toBeNull();
    expect(
      within(document.body).getByText(/Owner or Admin only/),
    ).toBeInTheDocument();
  });
});
