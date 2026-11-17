import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "src/utils/test-utils";
import { palette } from "src/theme/palette";

import IntegrationsList from "../IntegrationsList";
import IntegrationDetail from "../IntegrationDetail";

// Settings > Integrations stays readable at 1024: a platform card's badge
// drops under a long name, a connection card's footer texts keep a gap, a
// connection's header lets a long destination wrap and keeps its status and
// options menu in view, its field values wrap in their column, and its field
// labels read in secondary text. jsdom does not lay out, so each test checks
// the style that makes the row behave.

const h = vi.hoisted(() => ({ connections: [], detail: null }));

vi.mock("src/api/integrations", () => ({
  integrationKeys: { connections: () => ["integrations", "connections"] },
  useIntegrationConnections: () => ({
    data: h.connections,
    isLoading: false,
    isError: false,
  }),
  useStartSlackInstall: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useIntegrationConnection: () => ({
    data: h.detail,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useSyncNow: () => ({ mutate: vi.fn(), isPending: false }),
  usePauseConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useResumeConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateConnection: () => ({
    mutate: vi.fn(),
    isPending: false,
    error: null,
  }),
  useSyncLogs: () => ({ data: [], isLoading: false, isError: false }),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({
    allowed: true,
    reasonCode: null,
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("src/hooks/use-ag-theme", () => ({ useAgTheme: () => undefined }));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("../AddIntegrationWizard", () => ({ default: () => null }));
vi.mock("../DeleteIntegrationDialog", () => ({ default: () => null }));
vi.mock("../webhooks/WebhooksSection", () => ({ default: () => null }));

const LONG_BUCKET =
  "gatewayrequestlogarchiveforproductionworkloadsinuseast1region";

const connection = (id, platform, name, extra = {}) => ({
  id,
  platform,
  display_name: name,
  host_url: "https://us.i.posthog.com",
  status: "active",
  created_at: "2026-10-01T00:00:00Z",
  ...extra,
});

const detail = (platform, extra = {}) => ({
  id: "c0a80101-0000-4000-8000-000000000003",
  platform,
  display_name: "Request log archive",
  host_url: `https://${LONG_BUCKET}.s3.us-east-1.amazonaws.com/exports`,
  status: "active",
  external_project_name:
    "LangfuseProjectWithAVeryLongUnbrokenNameForReadability",
  project_name: "Readability",
  public_key_display: "pk-lf-0123456789abcdef0123456789abcdef0123456789abcdef",
  secret_key_display: "****",
  total_traces_synced: 0,
  total_spans_synced: 0,
  total_scores_synced: 0,
  sync_interval_seconds: 300,
  created_at: "2026-10-01T00:00:00Z",
  last_synced_at: "2026-10-01T00:05:00Z",
  backfill_completed: true,
  ...extra,
});

const withClient = (ui) => (
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
);
const renderList = () => render(withClient(<IntegrationsList />));
const renderDetail = () => render(withClient(<IntegrationDetail />));

const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const platformCard = (name) =>
  screen.getByRole("button", {
    name: new RegExp(`^${escape(name)}(?:\\s|$)`, "i"),
  });

beforeEach(() => {
  h.connections = [];
  h.detail = null;
});

describe("Integrations page stays readable at 1024", () => {
  it("a platform card's name row wraps its Connected badge under the name", () => {
    h.connections = [connection("c1", "cloud_storage", "Archive")];
    renderList();
    const name = within(platformCard("Cloud Storage")).getByText(
      "Cloud Storage",
    );
    const row = name.parentElement;
    expect(within(row).getByText("Connected")).toBeInTheDocument();
    expect(row).toHaveStyle({ flexWrap: "wrap" });
    expect(row.parentElement).toHaveStyle({ minWidth: "0" });
  });

  it("an export card's footer wraps with a gap instead of running its texts together", () => {
    h.connections = [
      connection("c1", "posthog", "Product analytics", {
        total_traces_synced: 1234,
        last_synced_at: "2026-10-01T00:05:00Z",
      }),
    ];
    renderList();
    const count = screen.getByText(
      /request log deliveries \(including repeats\)$/,
    );
    const footer = count.parentElement;
    expect(within(footer).getByText(/^Synced /)).toBeInTheDocument();
    expect(footer).toHaveStyle({ flexWrap: "wrap" });
  });
});

describe("Integration connection page stays readable at 1024", () => {
  it("the header wraps a long destination and keeps the status and menu in view", () => {
    h.detail = detail("cloud_storage");
    renderDetail();
    const destination = screen.getByTestId("integration-header-destination");
    expect(destination).toHaveTextContent(LONG_BUCKET);
    expect(destination).toHaveStyle({ overflowWrap: "anywhere" });
    expect(destination.previousElementSibling).toHaveStyle({
      overflowWrap: "anywhere",
    });
    const text = destination.parentElement;
    expect(text).toHaveStyle({ minWidth: "0" });
    expect(text.parentElement).toHaveStyle({ minWidth: "0" });
    expect(
      screen.getByRole("button", { name: "Integration options menu" })
        .parentElement,
    ).toHaveStyle({ flexShrink: "0" });
  });

  it("field labels and the empty sync history use secondary text, not the disabled colour", () => {
    h.detail = detail("datadog", { host_url: "https://api.datadoghq.com" });
    renderDetail();
    const secondary = palette("light").text.secondary;
    expect(screen.getByText("Platform")).toHaveStyle({ color: secondary });
    expect(screen.getByText("No sync history yet.")).toHaveStyle({
      color: secondary,
    });
  });

  it("a field value wraps inside its half-width column", () => {
    h.detail = detail("langfuse");
    renderDetail();
    expect(screen.getByText("Public Key").nextElementSibling).toHaveStyle({
      overflowWrap: "anywhere",
    });
    expect(screen.getByText("Langfuse Project").nextElementSibling).toHaveStyle(
      { overflowWrap: "anywhere" },
    );
  });
});
