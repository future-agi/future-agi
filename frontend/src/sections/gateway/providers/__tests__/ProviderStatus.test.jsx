// On the Providers list, each org provider's status comes from the org's own
// request logs of the last 24 h (unhealthy at 50% failed or more, degraded
// at 10% or more, from 5 requests); the operator's circuit breaker is never
// shown for it; a provider the config push leaves out reads "Not pushed",
// never a health chip. These are the provider status assertions, on the
// list and detail drawer.
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { render, screen, within } from "src/utils/test-utils";

import ProviderList from "../ProviderList";
import { buildProviderRows } from "../provider-rows";

vi.mock("ag-grid-react", async () => await import("./grid-mock"));
vi.mock("../AddProviderDialog", () => ({ default: () => null }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

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
      display_name: "OpenAI",
      status: "healthy",
      healthy: true,
      circuit_state: "n/a",
      pushed: true,
      status_reason: "0.0% of 40 requests failed in 24 h (healthy under 10%)",
      models: ["gpt-4o"],
      request_count: 40,
      avg_latency: 812.5,
      error_rate: 0,
      ...overrides,
    },
  ],
});

function renderList(overrides, props = {}) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ProviderList
        rows={buildProviderRows(CONFIG, null, health(overrides))}
        gatewayId="gw-1"
        canWrite
        {...props}
      />
    </QueryClientProvider>,
  );
}

const row = () => within(screen.getByTestId("ag-row"));

describe("Provider status on the Providers list", () => {
  it("renders degraded even though a degraded provider is healthy", () => {
    renderList({
      status: "degraded",
      healthy: true,
      error_rate: 12.5,
      status_reason:
        "12.5% of 40 requests failed in 24 h (degraded at 10% or more)",
    });

    expect(row().getByText("Degraded")).toBeInTheDocument();
    expect(row().queryByText("Healthy")).toBeNull();
    expect(
      row().getByText(
        "12.5% of 40 requests failed in 24 h (degraded at 10% or more)",
      ),
    ).toBeInTheDocument();
    expect(row().getByText("12.5%")).toBeInTheDocument();
  });

  it("states the thresholds the status comes from", () => {
    renderList({});

    expect(
      screen.getByText(
        /Status comes from your organization's own requests to each provider in the last 24 hours/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Unhealthy at 50% failed or more, Degraded at 10% or more/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/fewer than 5 requests read as Too few requests/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/read as Healthy/)).toBeNull();
  });

  // Requests served by managed models are left out of the status; a
  // deployment that offers none has none to leave out.
  it("names managed models in the status rule only where they are offered", () => {
    const { unmount } = renderList({});
    expect(
      screen.getByText(
        /Guardrail blocks and requests served on Future AGI credits are not counted/,
      ),
    ).toBeInTheDocument();
    unmount();

    renderList({}, { managedOffered: false });
    const rule = screen.getByText(
      /Status comes from your organization's own requests/,
    );
    expect(rule).not.toHaveTextContent(/managed|Future AGI credits/i);
    expect(rule).toHaveTextContent("Guardrail blocks are not counted.");
    expect(rule).toHaveTextContent(
      "fewer than 5 requests read as Too few requests",
    );
  });

  it("never shows a circuit breaker for an org provider", () => {
    renderList({ circuit_state: "open" });

    expect(screen.queryByText("Circuit Breaker")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: /circuit/i })).toBeNull();
    expect(screen.queryByText("open")).toBeNull();
  });

  it("labels the latency as the 24-hour average, not a P50", () => {
    renderList({});

    expect(screen.queryByText(/P50/)).toBeNull();
    expect(screen.getByText("Avg latency")).toBeInTheDocument();
    expect(
      screen.getByText(/Status, requests, latency and error rate: last 24 h/),
    ).toBeInTheDocument();
    expect(row().getByText("813 ms")).toBeInTheDocument();
  });

  it("shows a provider the push leaves out as not pushed, with the key", () => {
    renderList({
      status: "unhealthy",
      healthy: false,
      pushed: false,
      status_reason: "not pushed: unsupported credential key organization",
    });

    expect(row().getByText("Not pushed")).toBeInTheDocument();
    expect(row().queryByText("Healthy")).toBeNull();
    expect(row().queryByText("Unhealthy")).toBeNull();
    expect(
      row().getByText("not pushed: unsupported credential key organization"),
    ).toBeInTheDocument();
  });

  // Under the page's own rule (counted from 5 requests in 24 h) a status is
  // not measured: the badge says so, in neutral, never Healthy.
  it("shows a provider with fewer than 5 requests as Too few requests, never Healthy", () => {
    renderList({
      request_count: 3,
      error_rate: 100,
      status_reason: "fewer than 5 requests in 24 h",
    });

    expect(row().getByText("Too few requests")).toBeInTheDocument();
    expect(row().queryByText("Healthy")).toBeNull();
    expect(
      row().getByText("fewer than 5 requests in 24 h"),
    ).toBeInTheDocument();
  });

  it("repeats the status and its reason in the provider's detail", async () => {
    const user = userEvent.setup();
    renderList({
      status: "degraded",
      status_reason:
        "12.5% of 40 requests failed in 24 h (degraded at 10% or more)",
    });

    await user.click(screen.getByTestId("ag-row"));
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("Degraded")).toBeInTheDocument();
    expect(
      within(drawer).getByText(
        "12.5% of 40 requests failed in 24 h (degraded at 10% or more)",
      ),
    ).toBeInTheDocument();
  });
});
