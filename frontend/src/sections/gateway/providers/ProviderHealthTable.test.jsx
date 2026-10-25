// Provider Health shows the org's own health.
// Each org provider's status comes from the org's request logs of the last
// 24 h (unhealthy at 50% failed or more, degraded at 10% or more, from 5
// requests); the operator's circuit breaker is never shown for it; a provider
// the config push leaves out reads "Not pushed", never a healthy chip.
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import ProviderHealthTable from "./ProviderHealthTable";

const row = (overrides) => ({
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
});

function renderRows(rows) {
  return render(
    <ProviderHealthTable providerHealth={{ providers: rows }} orgConfig={{}} />,
  );
}

function rowFor(name) {
  return screen.getByText(name).closest("tr");
}

describe("Provider Health", () => {
  it("renders degraded even though a degraded provider is healthy", () => {
    renderRows([
      row({
        status: "degraded",
        healthy: true,
        error_rate: 12.5,
        status_reason:
          "12.5% of 40 requests failed in 24 h (degraded at 10% or more)",
      }),
    ]);

    const tr = within(rowFor("OpenAI"));
    expect(tr.getByText("Degraded")).toBeInTheDocument();
    expect(tr.queryByText("Healthy")).toBeNull();
    expect(
      tr.getByText(
        "12.5% of 40 requests failed in 24 h (degraded at 10% or more)",
      ),
    ).toBeInTheDocument();
    expect(tr.getByText("12.5%")).toBeInTheDocument();
  });

  it("states the thresholds the status comes from", () => {
    renderRows([row({})]);

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
      screen.getByText(/fewer than 5 requests read as Healthy/),
    ).toBeInTheDocument();
  });

  it("never shows a circuit breaker for an org provider", () => {
    renderRows([row({ circuit_state: "open" })]);

    expect(screen.queryByText("Circuit Breaker")).toBeNull();
    expect(screen.queryByText("open")).toBeNull();
  });

  it("labels the latency as the 24-hour average, not a P50", () => {
    renderRows([row({})]);

    expect(screen.queryByText("Latency (P50)")).toBeNull();
    expect(screen.getByText("Avg Latency (24 h)")).toBeInTheDocument();
    expect(within(rowFor("OpenAI")).getByText("813ms")).toBeInTheDocument();
  });

  it("shows a provider the push leaves out as not pushed, with the key", () => {
    renderRows([
      row({
        status: "unhealthy",
        healthy: false,
        pushed: false,
        status_reason: "not pushed: unsupported credential key organization",
      }),
    ]);

    const tr = within(rowFor("OpenAI"));
    expect(tr.getByText("Not pushed")).toBeInTheDocument();
    expect(tr.queryByText("Healthy")).toBeNull();
    expect(tr.queryByText("Unhealthy")).toBeNull();
    expect(
      tr.getByText("not pushed: unsupported credential key organization"),
    ).toBeInTheDocument();
  });

  it("shows too few requests as healthy with the reason", () => {
    renderRows([
      row({
        request_count: 3,
        error_rate: 100,
        status_reason: "fewer than 5 requests in 24 h",
      }),
    ]);

    const tr = within(rowFor("OpenAI"));
    expect(tr.getByText("Healthy")).toBeInTheDocument();
    expect(tr.getByText("fewer than 5 requests in 24 h")).toBeInTheDocument();
  });
});
