import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { assertContractedResponse } from "src/api/contracts/openapi-contract";

// Every axios response runs through assertContractedResponse (the response
// interceptor in src/utils/axios.js). Connections for platforms without a
// host (Datadog, queues, storage) are returned with host_url "", so the
// declared contract must accept it or the integrations list/detail break
// once strict response contracts are on.
const hostlessConnection = {
  id: "8f2b6a52-3d0c-4a7e-9c41-3f1d2e5b7a90",
  platform: "datadog",
  display_name: "Datadog",
  host_url: "",
  status: "active",
  status_message: "",
  external_project_name: "datadog",
  last_synced_at: null,
  total_traces_synced: 0,
  total_spans_synced: 0,
  total_scores_synced: 0,
  backfill_completed: true,
  backfill_progress: {},
  sync_interval_seconds: 300,
  created_at: "2026-10-07T12:00:00Z",
};

describe("integration connection response contracts", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", "true");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("accepts a connection list item with an empty host_url", () => {
    const response = {
      status: 200,
      config: { url: "/integrations/connections/", method: "get" },
      data: {
        status: true,
        result: {
          metadata: {
            total_count: 1,
            current_page: 0,
            page_size: 20,
            total_pages: 1,
            next_page: null,
          },
          connections: [hostlessConnection],
        },
      },
    };

    expect(() => assertContractedResponse(response)).not.toThrow();
  });

  it("accepts a created connection with an empty host_url", () => {
    const response = {
      status: 201,
      config: { url: "/integrations/connections/", method: "post" },
      data: { status: true, result: hostlessConnection },
    };

    expect(() => assertContractedResponse(response)).not.toThrow();
  });
});
