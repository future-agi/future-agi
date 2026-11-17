import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";

import IntegrationDetail from "../IntegrationDetail";
import EditIntegrationDialog from "../EditIntegrationDialog";

// An export connection's page and its Edit dialog show what an export has:
// its platform, schedule and sync history, not a Langfuse project, a key pair
// or trace, span and score totals, which only an import keeps.

const h = vi.hoisted(() => ({ detail: null, history: vi.fn() }));

vi.mock("src/api/integrations", () => ({
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
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("../IntegrationSyncHistory", () => ({
  default: (props) => {
    h.history(props);
    return null;
  },
}));
vi.mock("../DeleteIntegrationDialog", () => ({ default: () => null }));

const connection = (platform) => ({
  id: "c0a80101-0000-4000-8000-000000000002",
  platform,
  display_name: platform,
  host_url: `https://${platform}.com`,
  status: "active",
  external_project_name: platform,
  project_name: platform,
  public_key_display: "****",
  secret_key_display: "****",
  total_traces_synced: 0,
  total_spans_synced: 0,
  total_scores_synced: 0,
  sync_interval_seconds: 300,
  created_at: "2026-10-01T00:00:00Z",
  last_synced_at: "2026-10-01T00:05:00Z",
  backfill_completed: true,
});

const IMPORT_ONLY = [
  "Langfuse Project",
  "FutureAGI Project",
  "Public Key",
  "Secret Key",
  "Total Traces",
  "Total Spans",
  "Total Scores",
];

describe("Export connection page", () => {
  beforeEach(() => {
    h.history.mockClear();
  });

  it.each(["datadog", "posthog", "mixpanel", "cloud_storage"])(
    "%s shows its platform, schedule and sync history only",
    (platform) => {
      h.detail = connection(platform);
      render(<IntegrationDetail />);
      ["Platform", "Created", "Sync Interval", "Last Synced"].forEach((label) =>
        expect(screen.getByText(label)).toBeInTheDocument(),
      );
      IMPORT_ONLY.forEach((label) =>
        expect(screen.queryByText(label)).toBeNull(),
      );
      expect(h.history).toHaveBeenLastCalledWith(
        expect.objectContaining({ isExport: true }),
      );
    },
  );

  it("keeps the import fields for Langfuse", () => {
    h.detail = connection("langfuse");
    render(<IntegrationDetail />);
    IMPORT_ONLY.forEach((label) =>
      expect(screen.getByText(label)).toBeInTheDocument(),
    );
    expect(h.history).toHaveBeenLastCalledWith(
      expect.objectContaining({ isExport: false }),
    );
  });

  it.each([
    ["datadog", false],
    ["posthog", true],
  ])("%s edits its name and schedule (host: %s)", (platform, host) => {
    render(
      <EditIntegrationDialog
        open
        onClose={vi.fn()}
        connection={connection(platform)}
      />,
    );
    expect(screen.getByLabelText("Display Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Sync Interval")).toBeInTheDocument();
    expect(Boolean(screen.queryByLabelText("Host URL"))).toBe(host);
    expect(screen.queryByLabelText("Public Key")).toBeNull();
    expect(screen.queryByLabelText("Secret Key")).toBeNull();
  });
});
