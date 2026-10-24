// Every Settings value is the gateway's own: the cards read config.server,
// config.logging and config.batch as the config action passes them through
// from the gateway, and a block the gateway does not report reads "Not
// reported", never a default.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import GatewaySettingsSection from "./GatewaySettingsSection";

const mockRefetchConfig = vi.fn();
const mockHealthCheckPost = vi.fn();
const mockInvalidateQueries = vi.fn();
let mockTab = "settings";
let mockConfig;
let mockGateway;

const GATEWAY_REPORTED_CONFIG = {
  server: {
    host: "0.0.0.0",
    port: 8080,
    read_timeout_ms: 60000,
    write_timeout_ms: 300000,
    idle_timeout_ms: 120000,
    max_request_timeout_ms: 1500,
  },
  logging: {
    level: "info",
    request_logging: true,
    include_bodies: false,
    content_mode: "metadata_only",
  },
  batch: {
    storage: "memory",
    default_max_concurrency: 10,
    max_body_bytes: 10485760,
  },
  org_sections_enforced: ["alerting", "budgets", "ip_acl", "tool_policy"],
  last_health_check: "2026-10-21T15:04:05+00:00",
  last_health_check_status: "healthy",
  cost_tracking: { enabled: true },
};

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => ({ tab: mockTab }),
  };
});

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// Batch Jobs' Submit and Cancel are Owner or Admin only (useGatewayAdmin);
// these cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    post: (...args) => {
      mockHealthCheckPost(...args);
      return Promise.resolve({ data: { result: { status: "healthy" } } });
    },
  },
  endpoints: {
    gateway: { healthCheck: (id) => `/agentcc/gateways/${id}/health_check/` },
  },
}));

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual("@tanstack/react-query");
  return {
    ...actual,
    useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
    useMutation: (options) => ({
      isPending: false,
      mutate: async (variables) => {
        const result = await options.mutationFn(variables);
        options.onSuccess?.(result);
      },
    }),
  };
});

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: mockConfig,
    isLoading: false,
    refetch: mockRefetchConfig,
  }),
  useReloadConfig: () => ({ isPending: false, mutate: vi.fn() }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gateway-1",
    isLoading: false,
    gateway: mockGateway,
  }),
}));

vi.mock("./OrgConfigSection", () => ({ default: () => <div>Org config</div> }));
vi.mock("./EmailAlertsCard", () => ({
  default: () => <div>Email alerts</div>,
}));
vi.mock("./hooks/useBatchJobs", () => ({
  useSubmitBatch: () => ({ mutate: vi.fn(), isPending: false }),
  useBatchStatus: () => ({ data: null, isLoading: false }),
  useCancelBatch: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The value an InfoLine shows next to its label.
function valueOf(label) {
  const labelNode = screen.getByText(label, { selector: "p" });
  return labelNode.parentElement.lastChild.textContent;
}

function card(title) {
  return screen.getByRole("heading", { name: title }).closest(".MuiCard-root");
}

describe("Settings shows the gateway's own values", () => {
  beforeEach(() => {
    mockTab = "settings";
    mockConfig = structuredClone(GATEWAY_REPORTED_CONFIG);
    mockGateway = {
      id: "gateway-1",
      name: "Agent Command Center Gateway",
      baseUrl: "https://gateway.example.test/v1",
      status: "healthy",
      providerCount: 2,
      modelCount: 4,
      lastHealthCheck: null,
    };
    mockRefetchConfig.mockReset();
    mockHealthCheckPost.mockReset();
    mockInvalidateQueries.mockReset();
  });

  it("shows the server timeouts the gateway reports", () => {
    render(<GatewaySettingsSection />);

    expect(valueOf("Host")).toBe("0.0.0.0");
    expect(valueOf("Port")).toBe("8080");
    expect(valueOf("Read Timeout")).toBe("60 s");
    expect(valueOf("Write Timeout")).toBe("300 s");
    expect(valueOf("Idle Timeout")).toBe("120 s");
    expect(valueOf("Max Request Time")).toBe("1.5 s");
    // Neither is reported by the gateway: no card line that can only be a dash.
    expect(screen.queryByText("Shutdown Timeout")).toBeNull();
    expect(screen.queryByText("Format")).toBeNull();
  });

  it("shows the logging switches the gateway reports", () => {
    render(<GatewaySettingsSection />);

    expect(valueOf("Level")).toBe("info");
    expect(valueOf("Request Logging")).toBe("Enabled");
    expect(valueOf("Include Bodies")).toBe("No");
    expect(valueOf("Content Mode")).toBe("metadata_only");
  });

  it("says 'not reported' for a block the gateway does not send, never Disabled", () => {
    mockConfig = {
      server: { host: "0.0.0.0", port: 8080 },
      cost_tracking: {},
    };
    render(<GatewaySettingsSection />);

    const logging = within(card("Logging"));
    expect(logging.queryByText("Disabled")).toBeNull();
    expect(logging.queryByText("No")).toBeNull();
    expect(
      logging.getByText("This gateway did not report its logging settings."),
    ).toBeInTheDocument();
    expect(valueOf("Read Timeout")).toBe("Not reported");
    expect(valueOf("Org Sections Enforced")).toBe(
      "Not reported (enforcement unknown)",
    );
  });

  it("shows when the org last checked health and what it found", () => {
    render(<GatewaySettingsSection />);

    const shown = new Date("2026-10-21T15:04:05+00:00").toLocaleString();
    expect(valueOf("Last Health Check")).toBe(`${shown} (healthy)`);
  });

  it("falls back to the gateway list's time", () => {
    delete mockConfig.last_health_check;
    delete mockConfig.last_health_check_status;
    mockGateway.lastHealthCheck = "2026-10-20T08:00:00+00:00";
    mockGateway.last_health_check_status = "unreachable";
    render(<GatewaySettingsSection />);

    const shown = new Date("2026-10-20T08:00:00+00:00").toLocaleString();
    expect(valueOf("Last Health Check")).toBe(`${shown} (unreachable)`);
  });

  it("with no check recorded says so, never 'Never'", () => {
    delete mockConfig.last_health_check;
    delete mockConfig.last_health_check_status;
    render(<GatewaySettingsSection />);

    expect(valueOf("Last Health Check")).toBe("No check recorded");
    expect(screen.queryByText("Never")).toBeNull();
  });

  it("names the org sections this gateway enforces", () => {
    render(<GatewaySettingsSection />);

    expect(valueOf("Org Sections Enforced")).toBe(
      "alerting, budgets, ip_acl, tool_policy",
    );
  });

  it("names each org section the gateway does not enforce, with its reason", () => {
    mockConfig.org_sections_enforced = ["alerting", "budgets", "tool_policy"];
    mockConfig.org_sections_not_enforced = {
      ip_acl: "client IP unknown (trusted_proxies not set)",
    };
    render(<GatewaySettingsSection />);

    expect(valueOf("Org Sections Enforced")).toBe(
      "alerting, budgets, tool_policy",
    );
    expect(valueOf("Org Sections Not Enforced")).toBe(
      "ip_acl: client IP unknown (trusted_proxies not set)",
    );
  });

  it("shows no not-enforced line when the gateway reports none", () => {
    render(<GatewaySettingsSection />);

    expect(screen.queryByText("Org Sections Not Enforced")).toBeNull();
  });

  it("says when the gateway enforces no org section", () => {
    mockConfig.org_sections_enforced = [];
    render(<GatewaySettingsSection />);

    expect(valueOf("Org Sections Enforced")).toBe("None on this gateway");
  });

  it("re-reads the config after a health check", async () => {
    const user = userEvent.setup();
    render(<GatewaySettingsSection />);

    await user.click(screen.getByRole("button", { name: /health check/i }));

    expect(mockHealthCheckPost).toHaveBeenCalledWith(
      "/agentcc/gateways/gateway-1/health_check/",
      {},
    );
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["agentcc-gateways"],
    });
    expect(mockRefetchConfig).toHaveBeenCalled();
  });

  it("states the batch behaviour the gateway reports", () => {
    mockTab = "batch-jobs";
    render(<GatewaySettingsSection />);

    const batch = within(card("Batch API"));
    expect(valueOf("Storage")).toBe(
      "In this gateway node's memory: a restart loses its batches",
    );
    expect(valueOf("Concurrency")).toBe(
      "Set per submission (10 when a submission sets none)",
    );
    expect(valueOf("Largest Submission")).toBe("10 MiB");
    expect(batch.queryByText("Enabled")).toBeNull();
  });

  it("says when the gateway did not report its batch settings", () => {
    mockTab = "batch-jobs";
    delete mockConfig.batch;
    render(<GatewaySettingsSection />);

    expect(
      screen.getByText("This gateway did not report its batch settings."),
    ).toBeInTheDocument();
  });
});
