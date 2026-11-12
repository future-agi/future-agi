import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";

import IntegrationsList from "../IntegrationsList";
import IntegrationDetail from "../IntegrationDetail";

// A Langfuse import whose server no longer serves the trace list is in Error
// with a sentence saying why and since when. Its card on Settings >
// Integrations shows that sentence, and its connection page shows it once,
// with no "Backfill in progress" under an import that has stopped.

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

const STOPPED =
  "This Langfuse version isn't supported by the import. Import stopped on " +
  "16 Nov 2026: Langfuse no longer serves the API this import reads. " +
  "Everything already imported is unchanged.";

const langfuse = (extra = {}) => ({
  id: "c0a80101-0000-4000-8000-000000000004",
  platform: "langfuse",
  display_name: "Self-hosted Langfuse",
  host_url: "https://langfuse.example.com",
  status: "error",
  status_message: STOPPED,
  external_project_name: "support",
  project_name: "Support",
  public_key_display: "pk-lf-****abcd",
  secret_key_display: "sk-lf-****wxyz",
  total_traces_synced: 0,
  total_spans_synced: 0,
  total_scores_synced: 0,
  sync_interval_seconds: 60,
  created_at: "2026-11-11T14:40:00Z",
  last_synced_at: null,
  backfill_completed: false,
  backfill_progress: {},
  ...extra,
});

const withClient = (ui) => (
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
);
const renderList = () => render(withClient(<IntegrationsList />));
const renderDetail = () => render(withClient(<IntegrationDetail />));

beforeEach(() => {
  h.connections = [];
  h.detail = null;
});

describe("A Langfuse import its server no longer serves", () => {
  it("its card says the import stopped, since when, why, and that imported data is unchanged", () => {
    h.connections = [langfuse()];
    renderList();
    const reason = screen.getByTestId("integration-card-stopped-reason");
    expect(reason).toHaveTextContent(STOPPED);
    expect(reason).toHaveStyle({ overflowWrap: "anywhere" });
  });

  it("an active Langfuse card shows no stop reason", () => {
    h.connections = [
      langfuse({
        status: "active",
        status_message: "",
        last_synced_at: "2026-11-11T14:44:01Z",
        backfill_completed: true,
      }),
    ];
    renderList();
    expect(screen.queryByTestId("integration-card-stopped-reason")).toBeNull();
  });

  it("an export card in Error keeps its card as it was", () => {
    h.connections = [
      langfuse({
        id: "c0a80101-0000-4000-8000-000000000005",
        platform: "posthog",
        display_name: "Product analytics",
        host_url: "https://us.i.posthog.com",
        status_message: "PostHog answered HTTP 503.",
      }),
    ];
    renderList();
    expect(screen.queryByTestId("integration-card-stopped-reason")).toBeNull();
  });

  it("its connection page shows the sentence and no backfill in progress", () => {
    h.detail = langfuse();
    renderDetail();
    expect(screen.getByText(STOPPED)).toBeInTheDocument();
    expect(screen.queryByText(/Backfill in progress/)).toBeNull();
  });

  it("a backfill that is running still says so", () => {
    h.detail = langfuse({ status: "backfilling", status_message: "" });
    renderDetail();
    expect(screen.getByText(/Backfill in progress/)).toBeInTheDocument();
  });
});
