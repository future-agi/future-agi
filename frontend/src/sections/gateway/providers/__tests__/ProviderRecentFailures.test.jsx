// On the Providers list, "Recent failures (5 min)" shows the organization's
// own calls to each provider in the last 5 minutes and how many failed, from
// the providers read. It claims no circuit breaker state.
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "src/utils/test-utils";

import ProviderList from "../ProviderList";
import { buildProviderRows } from "../provider-rows";
import { lastGrid } from "./grid-mock";

vi.mock("ag-grid-react", async () => await import("./grid-mock"));
vi.mock("../AddProviderDialog", () => ({ default: () => null }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const HEADER = "Recent failures (5 min)";
const TOOLTIP =
  "Your organization's own calls to this provider in the last 5 minutes.";

const CONFIG = {
  providers: {
    openai: {
      display_name: "OpenAI",
      base_url: "https://api.openai.com/v1",
      api_format: "openai",
      models: ["gpt-4o"],
    },
  },
};

const health = (overrides) => ({
  providers: [
    {
      id: "openai",
      name: "openai",
      status: "healthy",
      healthy: true,
      circuit_state: "n/a",
      pushed: true,
      status_reason: "0.0% of 40 requests failed in 24 h (healthy under 10%)",
      request_count: 40,
      avg_latency: 812.5,
      error_rate: 0,
      ...overrides,
    },
  ],
});

function renderList(overrides) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ProviderList
        rows={buildProviderRows(CONFIG, null, health(overrides))}
        gatewayId="gw-1"
        canWrite
      />
    </QueryClientProvider>,
  );
}

// The cell under the Recent failures header in the provider's row.
function recentCell() {
  const headers = screen.getAllByRole("columnheader");
  const index = headers.findIndex((th) => th.textContent === HEADER);
  expect(index).toBeGreaterThanOrEqual(0);
  return within(screen.getByTestId("ag-row")).getAllByRole("cell")[index];
}

describe("Recent failures (5 min) on the Providers list", () => {
  it("shows how many of the recent calls failed", () => {
    renderList({ recent_calls: 8, recent_failures: 3 });
    expect(recentCell()).toHaveTextContent(/^3 of 8 calls$/);
  });

  it('reads "None" when no recent call failed', () => {
    renderList({ recent_calls: 8, recent_failures: 0 });
    expect(recentCell()).toHaveTextContent(/^None$/);
  });

  it('reads "No calls" when there was no recent call', () => {
    renderList({ recent_calls: 0, recent_failures: 0 });
    expect(recentCell()).toHaveTextContent(/^No calls$/);
  });

  it("shows no figure when the read gave none", () => {
    renderList({});
    expect(recentCell()).toHaveTextContent(/^—$/);
  });

  it("says whose calls and over what time, and names no breaker", () => {
    renderList({ recent_calls: 8, recent_failures: 3 });
    const column = lastGrid.props.columnDefs.find(
      (col) => col.headerName === HEADER,
    );
    expect(column.headerTooltip).toBe(TOOLTIP);
    // The Status (24 h) column stays as it is.
    expect(
      screen.getByRole("columnheader", { name: "Status (24 h)" }),
    ).toBeInTheDocument();
    const words = /circuit breaker|\bclosed\b|\bopen\b|half-open/i;
    lastGrid.props.columnDefs.forEach((col) => {
      const text = `${col.headerName} ${col.headerTooltip || ""}`;
      expect(text).not.toMatch(words);
    });
    expect(screen.getByTestId("ag-grid")).not.toHaveTextContent(
      /circuit breaker|half-open/i,
    );
  });
});
