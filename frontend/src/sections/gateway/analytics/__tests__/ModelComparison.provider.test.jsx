import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";

import ModelComparison from "../ModelComparison";
import { grid } from "../../logs/__tests__/fakeAgGrid";
import { CELL_PADDING_PX, CELL_TEXT_PX } from "../../logs/__tests__/gridFit";

// Customer screens name a managed call's provider "Future AGI". The
// model-comparison API says each row's billing_mode, so the Provider column
// labels a row from its billing mode, never from the provider's name: the
// org's own OpenRouter key keeps "openrouter". The filter and the sort read
// the label the column shows.

vi.mock("ag-grid-react", () => import("../../logs/__tests__/fakeAgGrid"));

const mockUseAnalyticsModels = vi.fn();
vi.mock("../hooks/useAnalyticsModels", () => ({
  useAnalyticsModels: (...args) => mockUseAnalyticsModels(...args),
}));

const row = (overrides) => ({
  request_count: 1,
  total_tokens: 10,
  total_cost: "0.0001",
  avg_cost_per_request: "0.0001",
  avg_latency_ms: 100,
  p95_latency_ms: 200,
  error_rate: 0,
  cache_hit_rate: 0,
  tokens_per_second: 10,
  ...overrides,
});

const MODELS = [
  row({
    model: "anthropic/claude-sonnet-5.5",
    provider: "openrouter",
    billing_mode: "managed",
    request_count: 30,
  }),
  row({
    model: "openai/gpt-6-sol",
    provider: "openrouter",
    billing_mode: "byok",
    request_count: 20,
  }),
  row({
    model: "gpt-4o",
    provider: "openai",
    billing_mode: null,
    request_count: 10,
  }),
];

describe("Model request links", () => {
  const start = "2026-11-09T01:02:03.456Z";
  const end = "2026-11-09T02:03:04.567Z";
  beforeEach(() => {
    mockUseAnalyticsModels.mockReset();
    mockUseAnalyticsModels.mockReturnValue({
      isLoading: false,
      data: { models: MODELS },
    });
  });

  it("links all requests for a model, not just its dominant provider or billing mode", () => {
    render(<ModelComparison start={start} end={end} gatewayId="gw-1" />);
    const links = screen.getAllByRole("link", { name: /^View requests for/ });
    expect(links).toHaveLength(3);
    links.forEach((link, i) => {
      expect(link.tagName).toBe("A");
      const expected = {
        started_after: start,
        started_before: end,
        gateway_id: "gw-1",
        model: MODELS[i].model,
      };
      expect(Object.fromEntries(new URL(link.href).searchParams)).toEqual(
        expected,
      );
      link.focus();
      expect(link).toHaveFocus();
    });
  });

  it("adds billing mode only when it is an aggregation key, never the dominant provider", () => {
    mockUseAnalyticsModels.mockReturnValue({
      data: { group_by: "billing_mode", models: [MODELS[1]] },
    });
    render(<ModelComparison start={start} end={end} />);
    const link = screen.getByRole("link", { name: /^View requests for/ });
    expect(Object.fromEntries(new URL(link.href).searchParams)).toEqual({
      started_after: start,
      started_before: end,
      model: MODELS[1].model,
      billing_mode: "byok",
    });
  });

  it("uses an explicitly unscoped range for an unrecorded billing-mode group", () => {
    mockUseAnalyticsModels.mockReturnValue({
      data: { group_by: "billing_mode", models: [MODELS[2]] },
    });
    render(<ModelComparison start={start} end={end} />);
    expect(
      screen.getByRole("link", { name: "View range for gpt-4o" }),
    ).toBeInTheDocument();
  });

  it("does not turn an unknown or aggregate model into an exact filter", () => {
    mockUseAnalyticsModels.mockReturnValue({
      isLoading: false,
      data: { models: [row({ model: "Unknown", provider: "openai" })] },
    });
    render(<ModelComparison start={start} end={end} />);
    const link = screen.getByRole("link", { name: "View range for Unknown" });
    expect(Object.fromEntries(new URL(link.href).searchParams)).toEqual({
      started_after: start,
      started_before: end,
    });
  });

  it("hides previous-range links while a new range is pending", () => {
    mockUseAnalyticsModels.mockReturnValue({
      isLoading: false,
      isPlaceholderData: true,
      isFetching: true,
      data: { models: MODELS },
    });
    render(<ModelComparison start={start} end={end} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("retains models with a stale warning on refresh failure", () => {
    mockUseAnalyticsModels.mockReturnValue({
      isLoading: false,
      error: new Error("Refresh failed"),
      data: { models: MODELS },
      refetch: vi.fn(),
    });
    render(<ModelComparison start={start} end={end} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Showing stale data");
    expect(
      screen.getAllByRole("link", { name: /^View requests for/ }),
    ).toHaveLength(3);
  });
});

const bodyRows = () =>
  within(screen.getByTestId("ag-grid")).getAllByRole("row").slice(1);

describe("Model comparison: Provider follows the billing mode", () => {
  beforeEach(() => {
    mockUseAnalyticsModels.mockReset();
    mockUseAnalyticsModels.mockReturnValue({
      isLoading: false,
      error: null,
      data: { group_by: "model", models: MODELS },
      refetch: vi.fn(),
    });
  });

  it("names a managed row's provider Future AGI", () => {
    render(<ModelComparison start="2026-10-01" end="2026-10-22" />);
    const [managed, ownKey, plain] = bodyRows();
    expect(managed).toHaveTextContent("Future AGI");
    expect(managed).not.toHaveTextContent("openrouter");
    // The org's own OpenRouter key is a provider of its own.
    expect(ownKey).toHaveTextContent("openrouter");
    expect(ownKey).not.toHaveTextContent("Future AGI");
    expect(plain).toHaveTextContent("openai");
  });

  it("filters on the provider the column shows", async () => {
    const user = userEvent.setup();
    render(<ModelComparison start="2026-10-01" end="2026-10-22" />);
    const search = screen.getByPlaceholderText("Filter by model or provider");

    await user.type(search, "Future AGI");
    expect(bodyRows()).toHaveLength(1);
    expect(bodyRows()[0]).toHaveTextContent("anthropic/claude-sonnet-5.5");

    await user.clear(search);
    await user.type(search, "openrouter");
    expect(bodyRows()).toHaveLength(1);
    expect(bodyRows()[0]).toHaveTextContent("openai/gpt-6-sol");
  });

  it("sorts the Provider column by the label it shows", () => {
    render(<ModelComparison start="2026-10-01" end="2026-10-22" />);
    const provider = grid.props.columnDefs.find(
      (c) => c.headerName === "Provider",
    );
    expect(provider.valueGetter({ data: MODELS[0] })).toBe("Future AGI");
    expect(provider.valueGetter({ data: MODELS[1] })).toBe("openrouter");
  });

  // A row per model and billing mode (group_by=billing_mode) can repeat a
  // model and a provider.
  it("keeps rows of one model and provider apart by billing mode", () => {
    render(<ModelComparison start="2026-10-01" end="2026-10-22" />);
    const id = (data) => grid.props.getRowId({ data });
    expect(
      id({ model: "m", provider: "anthropic", billing_mode: "byok" }),
    ).not.toBe(id({ model: "m", provider: "anthropic", billing_mode: null }));
  });
});

// A narrow Model comparison column must not cut the "Future AGI"
// label or the model ids. The Provider column holds the whole label at its
// narrowest, the Model column the demo's longest id, and a longer id is
// never cut without its tooltip.
describe("Model comparison: the label and the model id are shown whole", () => {
  beforeEach(() => {
    mockUseAnalyticsModels.mockReset();
    mockUseAnalyticsModels.mockReturnValue({
      isLoading: false,
      error: null,
      data: { group_by: "model", models: MODELS },
      refetch: vi.fn(),
    });
  });
  const column = (name) =>
    grid.props.columnDefs.find((c) => c.headerName === name);

  it("keeps the longer legacy label width budget in the Provider column", () => {
    render(<ModelComparison start="2026-10-01" end="2026-10-22" />);
    expect(column("Provider").minWidth).toBeGreaterThanOrEqual(
      CELL_TEXT_PX["Future AGI managed"] + CELL_PADDING_PX,
    );
  });

  it("holds the longest demo model id in the Model column", () => {
    render(<ModelComparison start="2026-10-01" end="2026-10-22" />);
    expect(column("Model").minWidth).toBeGreaterThanOrEqual(
      CELL_TEXT_PX["meta-llama/llama-3.1-8b-instruct"] + CELL_PADDING_PX,
    );
  });

  it("gives a longer model id and provider their tooltip", () => {
    render(<ModelComparison start="2026-10-01" end="2026-10-22" />);
    const id = "some-vendor/an-unusually-long-model-identifier-20261023";
    const { cellRenderer: Model } = column("Model");
    const { container } = render(<Model value={id} />);
    expect(container.querySelector(`[title="${id}"]`)).toHaveTextContent(id);
    const { cellRenderer: Provider } = column("Provider");
    const shown = render(<Provider value="Future AGI" />);
    expect(
      shown.container.querySelector('[title="Future AGI"]'),
    ).toBeInTheDocument();
  });
});

// o1-mini read "$0.00" for Cost and
// Cost / request over 1,604 tokens while Logs read its rows "No price found".
// The API now counts each row's unpriced requests (stored at cost 0): a row
// whose every request had no price reads an em dash titled No price found,
// one with some reads its priced part as a floor, never as free.
describe("Model comparison: unpriced cost is never free", () => {
  const UNPRICED = [
    row({
      model: "o1-mini",
      provider: "openai",
      request_count: 2,
      total_tokens: 1604,
      total_cost: "0",
      avg_cost_per_request: "0",
      unpriced_requests: 2,
    }),
    row({
      model: "gpt-4o",
      provider: "openai",
      request_count: 2,
      total_cost: "0.005",
      avg_cost_per_request: "0.0025",
      unpriced_requests: 1,
    }),
  ];
  beforeEach(() => {
    mockUseAnalyticsModels.mockReset();
    mockUseAnalyticsModels.mockReturnValue({
      isLoading: false,
      error: null,
      data: { group_by: "model", models: UNPRICED },
      refetch: vi.fn(),
    });
  });
  const column = (name) =>
    grid.props.columnDefs.find((c) => c.headerName === name);

  it("reads a row with no priced request as unknown in Cost and Cost / request", () => {
    render(<ModelComparison start="2026-10-01" end="2026-10-22" />);
    const [unpriced] = bodyRows();
    expect(unpriced).not.toHaveTextContent("$0.00");
    for (const name of ["Cost", "Cost / request"]) {
      const col = column(name);
      const value = col.valueGetter({ data: UNPRICED[0] });
      expect(value, name).toBeNull();
      expect(col.valueFormatter({ value, data: UNPRICED[0] }), name).toBe("—");
      expect(col.tooltipValueGetter({ value, data: UNPRICED[0] }), name).toBe(
        "No price found",
      );
    }
  });

  it("reads a row with some unpriced requests as a floor, saying how many", () => {
    render(<ModelComparison start="2026-10-01" end="2026-10-22" />);
    const cost = column("Cost");
    const value = cost.valueGetter({ data: UNPRICED[1] });
    // Lower bounds use exact amounts, not cell precision.
    expect(cost.valueFormatter({ value, data: UNPRICED[1] })).toBe("≥ $0.005");
    expect(cost.tooltipValueGetter({ value, data: UNPRICED[1] })).toBe(
      "1 of 2 requests had no price found, so this total counts only the priced ones.",
    );
    const each = column("Cost / request");
    const avg = each.valueGetter({ data: UNPRICED[1] });
    expect(each.valueFormatter({ value: avg, data: UNPRICED[1] })).toBe(
      "≥ $0.0025",
    );
  });
});

// o1-mini read "≥ $0.00" with 20 of 30 requests unpriced.
describe("Model comparison: a priced part of 0 is unknown", () => {
  const ZERO = row({
    model: "o1-mini",
    provider: "flaky-upstream",
    request_count: 30,
    total_cost: "0",
    avg_cost_per_request: "0",
    unpriced_requests: 20,
  });
  beforeEach(() => {
    mockUseAnalyticsModels.mockReset();
    mockUseAnalyticsModels.mockReturnValue({
      isLoading: false,
      error: null,
      data: { group_by: "model", models: [ZERO] },
      refetch: vi.fn(),
    });
  });

  it("reads Cost and Cost / request as unknown, sorting as missing", () => {
    render(<ModelComparison start="2026-10-01" end="2026-10-22" />);
    expect(bodyRows()[0]).not.toHaveTextContent("$0.00");
    for (const name of ["Cost", "Cost / request"]) {
      const col = grid.props.columnDefs.find((c) => c.headerName === name);
      const value = col.valueGetter({ data: ZERO });
      expect(value, name).toBeNull();
      expect(col.tooltipValueGetter({ value, data: ZERO }), name).toBe(
        "No price found for 20 of 30 requests",
      );
    }
  });
});
