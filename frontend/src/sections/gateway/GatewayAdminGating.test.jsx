import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ProviderManagementSection from "./providers/ProviderManagementSection";
import ProviderConfigView from "./providers/ProviderConfigView";
import MCPManagementSection from "./mcp/MCPManagementSection";
import ExperimentsSection from "./experiments/ExperimentsSection";
import ExperimentDetailSection from "./experiments/ExperimentDetailSection";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "./testing/adminOnly";

// The role check in the console. The backend answers 403 to a Member or a Viewer
// on every gateway config write. These screens are covered here; the
// others (Fallbacks, Guardrails, Budgets, Monitoring, Routing, the Org
// Config editor, its version history, MCP servers and guardrails) carry the
// same cases in their own test files. Every screen is readable by every member.

let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

let mockParams = {};
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => mockParams,
  useNavigate: () => vi.fn(),
}));

const mutation = () => ({
  mutate: vi.fn(),
  isPending: false,
  isError: false,
  error: null,
});

const PROVIDERS = {
  openai: { base_url: "https://api.openai.com/v1", models: ["gpt-4o"] },
};

vi.mock("./context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));

vi.mock("./providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: { providers: PROVIDERS, mcp: { enabled: true, servers: {} } },
    isLoading: false,
  }),
  useProviderHealth: () => ({ data: { providers: [] }, isLoading: false }),
  useReloadConfig: () => mutation(),
  useRemoveProvider: () => mutation(),
}));

vi.mock("./providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null, isLoading: false }),
}));

vi.mock("./providers/AddProviderDialog", () => ({ default: () => null }));
vi.mock("./providers/ProviderHealthTable", () => ({
  default: () => <div>Provider health table</div>,
}));

vi.mock("./mcp/hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: { enabled: true, servers: [] },
    isLoading: false,
  }),
  useMCPTools: () => ({ data: [], isLoading: false }),
  useMCPResources: () => ({ data: [], isLoading: false }),
  useMCPPrompts: () => ({ data: [], isLoading: false }),
}));
vi.mock("./mcp/MCPOverviewTab", () => ({
  default: () => <div>MCP overview</div>,
}));
vi.mock("./mcp/AddMCPServerDialog", () => ({ default: () => null }));

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

beforeEach(() => {
  mockOrgLevel = 8;
  mockParams = {};
  mockDetail = EXPERIMENTS[0];
});

describe("Providers", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s sees Add Provider disabled and Reload open",
    (_who, level) => {
      mockOrgLevel = level;
      render(<ProviderManagementSection />);
      expect(screen.getByText("Provider health table")).toBeInTheDocument();
      // Members and Viewers both see this button, disabled, with the reason.
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

describe("MCP", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s sees Add Server disabled and Reload open",
    (_who, level) => {
      mockOrgLevel = level;
      render(<MCPManagementSection />);
      expect(screen.getByText("MCP overview")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Add Server" }));
      expect(
        screen.getByRole("button", { name: "Reload Config" }),
      ).toBeEnabled();
    },
  );

  it.each(ADMIN_LEVELS)("%s can add a server", (_who, level) => {
    mockOrgLevel = level;
    render(<MCPManagementSection />);
    expectEditable(screen.getByRole("button", { name: "Add Server" }));
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
