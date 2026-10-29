import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "src/utils/test-utils";
import { validateContractedResponse } from "src/api/contracts/openapi-contract";
import { endpoints } from "src/utils/axios";
import WidgetChart from "../WidgetChart";
import { isPinnedWindowExcluded } from "../dashboardDateRange";
import fixture from "./fixtures/savedMetricResponse.json";

const h = vi.hoisted(() => ({
  query: { data: null, isPending: false, isError: false, mutate: vi.fn() },
}));
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardQuery: () => h.query,
}));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
let response;
let widget;
const clone = (value) => JSON.parse(JSON.stringify(value));
const metric = () => response.result.metrics[0];
const context = () => metric().derived_context;
const sync = () => {
  h.query.data = { data: response };
};
beforeEach(() => {
  vi.clearAllMocks();
  response = clone(fixture.response);
  widget = {
    id: "widget",
    query_config: clone(fixture.config),
    chart_config: { chart_type: "metric" },
    description: "Current draft v2: sum(cost)",
  };
  h.query.isPending = false;
  h.query.isError = false;
  sync();
});
const validate = (data) =>
  validateContractedResponse({
    config: { method: "post", url: endpoints.dashboard.query },
    status: 200,
    data,
  });

describe("source-produced saved scalar context", () => {
  it("accepts current and older backend contracts but rejects malformed optional provenance", () => {
    expect(validate(response).ok).toBe(true);
    delete context().run.definition;
    expect(validate(response).ok).toBe(true);
    delete metric().derived_context;
    expect(validate(response).ok).toBe(true);
    metric().derived_context = clone(
      fixture.response.result.metrics[0].derived_context,
    );
    context().run.metric_version = "not a version";
    expect(validate(response).ok).toBe(false);
  });
  it("puts immutable v1 formula, unit, totals, exclusions and UTC beside the scalar", () => {
    render(<WidgetChart widget={widget} />);
    expect(screen.getByText("0.50")).toBeInTheDocument();
    const region = screen.getByRole("region", { name: "Saved result context" });
    expect(region).toHaveTextContent(/version 1/);
    expect(region).toHaveTextContent("ratio(resolved == 1, resolved >= 0)");
    expect(region).toHaveTextContent(
      /3 session units.*2 valid.*1 missing inputs.*0 invalid probability/,
    );
    expect(region).toHaveTextContent(
      /Numerator total: 1.*denominator total: 2/,
    );
    expect(region).toHaveTextContent(/Unweighted.*selection unit: session/);
    expect(region).toHaveTextContent(/UTC/);
    expect(region).not.toHaveTextContent(/sum\(cost\)|Current draft|version 2/);
    expect(screen.getByText(/Point estimate only/)).toBeInTheDocument();
    expect(
      screen.getByText(/No 95% interval: unstable variance/),
    ).toBeInTheDocument();
    expect(h.query.mutate).toHaveBeenCalledTimes(1);
  });
  it.each(["queued", "running", "failed"])(
    "labels the last committed scalar while recompute is %s",
    (status) => {
      context().run.status = status;
      sync();
      render(<WidgetChart widget={widget} />);
      const region = screen.getByRole("region", {
        name: "Saved result context",
      });
      expect(region).toHaveTextContent(`Recompute ${status}`);
      expect(region).toHaveTextContent(
        /last committed result, not a new result/,
      );
      expect(screen.getByText("0.50")).toBeInTheDocument();
    },
  );
  it.each(["none", "inverse_probability"])(
    "keeps %s arbitrary totals neutral, never percentages or unit counts",
    (weighting) => {
      context().run.definition.weighting = weighting;
      context().run.definition.formula = "ratio(cost, tokens)";
      Object.assign(context().result, {
        value: 2.5,
        numerator: 5,
        denominator: 2,
      });
      metric().series[0].data[0].value = 2.5;
      sync();
      render(<WidgetChart widget={widget} />);
      const region = screen.getByRole("region", {
        name: "Saved result context",
      });
      expect(region).toHaveTextContent(/numerator total: 5/i);
      expect(region).not.toHaveTextContent(/5 of 2|250%|satisfies/);
      expect(screen.getByText("2.50")).toBeInTheDocument();
    },
  );
  it.each([0, null])(
    "keeps committed %s and diagnostics without inferring zero",
    (value) => {
      metric().series[0].data[0].value = value;
      Object.assign(context().result, {
        value,
        numerator: value,
        status: value === null ? "invalid_probability" : "ok",
        invalid_probability_count: value === null ? 1 : 0,
      });
      sync();
      render(<WidgetChart widget={widget} />);
      if (value === 0) expect(screen.getByText("0.00")).toBeInTheDocument();
      else {
        expect(screen.queryByText("0.00")).not.toBeInTheDocument();
        expect(
          screen.getByText(/No value — invalid probability.*not zero/),
        ).toBeInTheDocument();
        expect(
          screen.queryByText(/Point estimate only/),
        ).not.toBeInTheDocument();
      }
    },
  );
  it("updates saved context and scalar together on a new result identity", () => {
    const view = render(<WidgetChart widget={widget} />);
    response = clone(response);
    context().run.result_id = "00000000-0000-0000-0000-000000000005";
    Object.assign(context().result, { value: 0, numerator: 0 });
    metric().series[0].data[0].value = 0;
    sync();
    view.rerender(<WidgetChart widget={widget} refreshRequestId={1} />);
    // A mutation's new data is accepted via its onSuccess exactness gate,
    // not by mutating the hook's pre-seeded first-render value.
    act(() =>
      h.query.mutate.mock.calls.at(-1)[1].onSuccess({ data: response }),
    );
    expect(screen.getByText("0.00")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Saved result context" }),
    ).toHaveTextContent(/Numerator total: 0/);
    expect(screen.queryByText("0.50")).not.toBeInTheDocument();
  });
  it.each([
    "absent",
    "different run",
    "different observation",
    "different value",
    "different version",
    "multiple series",
  ])("reports unavailable instead of inferring %s context", (kind) => {
    if (kind === "absent") delete metric().derived_context;
    if (kind === "different run") context().run.id = "other";
    if (kind === "different observation")
      context().run.window_end = "2026-10-27T00:00:00Z";
    if (kind === "different value") context().result.value = 99;
    if (kind === "different version") context().run.definition.version = 2;
    if (kind === "multiple series")
      metric().series.push({ ...clone(metric().series[0]), name: "other" });
    sync();
    render(<WidgetChart widget={widget} />);
    expect(
      screen.getAllByText(/Saved formula and diagnostic context unavailable/)
        .length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByRole("region", { name: "Saved result context" }),
    ).not.toBeInTheDocument();
  });
  it("does not manufacture a missing saved definition from a description", () => {
    delete context().run.definition;
    sync();
    render(<WidgetChart widget={widget} />);
    const region = screen.getByRole("region", { name: "Saved result context" });
    expect(region).toHaveTextContent(/Saved definition unavailable/);
    expect(region).not.toHaveTextContent(/Unweighted|sum\(cost\)/);
  });
  it("associates multiple pinned metrics by response identity rather than name or order", () => {
    const second = clone(metric());
    second.id = "second";
    second.derived_context.run.id = "second-run";
    second.derived_context.run.definition.formula = "sum(cost)";
    second.derived_context.run.definition.selection_unit = "user";
    response.result.metrics.unshift(second);
    widget.query_config.metrics.push({
      ...clone(widget.query_config.metrics[0]),
      id: "second",
      derived_run_id: "second-run",
    });
    sync();
    render(<WidgetChart widget={widget} />);
    const regions = screen.getAllByRole("region", {
      name: "Saved result context",
    });
    expect(regions).toHaveLength(2);
    expect(regions[0]).toHaveTextContent(/sum\(cost\)/);
    expect(regions[1]).toHaveTextContent(/ratio\(resolved/);
  });
  it("preserves non-derived scalar semantics and does not add provenance copy", () => {
    widget.query_config.metrics = [
      { id: metric().id, name: "Cost", type: "cost", aggregation: "avg" },
    ];
    render(<WidgetChart widget={widget} />);
    expect(screen.getByText("0.50")).toBeInTheDocument();
    expect(screen.queryByText(/context unavailable/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Saved result context" }),
    ).not.toBeInTheDocument();
  });
  it("does not blame a failed read or show context from an unsuccessful response", () => {
    h.query.data = null;
    h.query.isError = true;
    render(
      <WidgetChart
        widget={widget}
        globalDateRange={{ start: "2026-10-23", end: "2026-10-24" }}
      />,
    );
    expect(screen.queryByText(/outside/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Saved result context" }),
    ).not.toBeInTheDocument();
  });
  it("offers precise recovery for an overlapping range that excludes the saved observation", () => {
    metric().series[0].data = [];
    delete context().result;
    sync();
    render(
      <WidgetChart
        widget={widget}
        globalDateRange={{ start: "2026-10-23", end: "2026-10-24" }}
        onRestoreSavedWindow={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/saved observation.*is outside/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Restore saved window" }),
    ).toBeInTheDocument();
  });
});

describe("saved observation inclusive date rule", () => {
  it.each([
    ["2026-09-28", "2026-09-29", true],
    ["2026-10-27", "2026-10-28", true],
    ["2026-10-23", "2026-10-24", true],
    ["2026-10-26", "2026-10-27", false],
    ["2026-10-25", "2026-10-26", false],
    ["2026-10-01", "2026-10-27", false],
    ["2026-10-26", "2026-10-26", false],
    ["2026-10-27", "2026-10-27", true],
    ["invalid", "2026-10-27", false],
    ["2026-10-27", "2026-10-25", false],
  ])("%s to %s excluded=%s", (start, end, excluded) => {
    expect(isPinnedWindowExcluded(fixture.config, { start, end })).toBe(
      excluded,
    );
  });
  it("prefers the actual immutable response window over an edited widget range", () => {
    const range = { start: "2026-10-23", end: "2026-10-24" };
    const edited = {
      ...fixture.config,
      time_range: { custom_start: range.start, custom_end: range.end },
    };
    const savedRun = fixture.response.result.metrics[0].derived_context.run;
    expect(isPinnedWindowExcluded(edited, range, savedRun)).toBe(true);
    expect(
      isPinnedWindowExcluded(edited, range, {
        ...savedRun,
        id: "different-run",
      }),
    ).toBe(false);
    expect(
      isPinnedWindowExcluded(edited, range, {
        ...savedRun,
        window_end: undefined,
      }),
    ).toBe(false);
  });
  it("does not claim provenance for unknown timestamps or mixed metrics", () => {
    const range = { start: "2026-10-23", end: "2026-10-24" };
    expect(
      isPinnedWindowExcluded(
        { ...fixture.config, time_range: { preset: "7D" } },
        range,
      ),
    ).toBe(false);
    expect(
      isPinnedWindowExcluded(
        {
          ...fixture.config,
          metrics: [...fixture.config.metrics, { type: "cost" }],
        },
        range,
      ),
    ).toBe(false);
    expect(
      isPinnedWindowExcluded(
        {
          ...fixture.config,
          time_range: { ...fixture.config.time_range, custom_end: "invalid" },
        },
        range,
      ),
    ).toBe(false);
  });
});
