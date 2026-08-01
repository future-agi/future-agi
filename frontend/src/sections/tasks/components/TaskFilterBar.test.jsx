import React from "react";
import PropTypes from "prop-types";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { useForm } from "react-hook-form";
import { describe, expect, it, vi } from "vitest";

import TaskFilterBar from "./TaskFilterBar";

vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: ({ tab, source, isSpansView, currentFilters, onApply }) => (
    <>
      <div
        data-testid="trace-filter-panel"
        data-tab={tab || ""}
        data-source={source}
        data-spans-view={String(isSpansView)}
        data-api-col-type={currentFilters?.[0]?.apiColType || ""}
      />
      <button type="button" onClick={() => onApply(currentFilters)}>
        Apply current filters
      </button>
    </>
  ),
  useTraceFilterProperties: () => ({ data: [] }),
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: () => ({ data: [] }),
}));

const Harness = ({ rowType, defaultFilters = [] }) => {
  const { control, setValue, watch } = useForm({
    defaultValues: { filters: defaultFilters },
  });
  return (
    <>
      <TaskFilterBar
        control={control}
        setValue={setValue}
        projectId="project-1"
        rowType={rowType}
      />
      <output data-testid="form-filters">
        {JSON.stringify(watch("filters"))}
      </output>
    </>
  );
};

Harness.propTypes = {
  rowType: PropTypes.string.isRequired,
  defaultFilters: PropTypes.array,
};

describe("TaskFilterBar filter context", () => {
  it("requests span fields and span values for span tasks", () => {
    render(<Harness rowType="spans" />);

    const panel = screen.getByTestId("trace-filter-panel");
    expect(panel).toHaveAttribute("data-tab", "spans");
    expect(panel).toHaveAttribute("data-source", "spans");
    expect(panel).toHaveAttribute("data-spans-view", "true");
  });

  it("keeps trace tasks in trace context", () => {
    render(<Harness rowType="traces" />);

    const panel = screen.getByTestId("trace-filter-panel");
    expect(panel).toHaveAttribute("data-tab", "trace");
    expect(panel).toHaveAttribute("data-source", "traces");
    expect(panel).toHaveAttribute("data-spans-view", "false");
  });

  it("preserves apiColType when multi-value rows hydrate and apply", () => {
    const defaultFilters = ["enterprise", "startup"].map((value) => ({
      property: "attributes",
      propertyId: "customer_tier",
      fieldCategory: "attribute",
      apiColType: "RESOURCE_ATTRIBUTE",
      filterConfig: {
        filterType: "text",
        filterOp: "equals",
        filterValue: value,
      },
    }));

    render(<Harness rowType="spans" defaultFilters={defaultFilters} />);

    expect(screen.getByTestId("trace-filter-panel")).toHaveAttribute(
      "data-api-col-type",
      "RESOURCE_ATTRIBUTE",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Apply current filters" }),
    );

    const roundTripped = JSON.parse(
      screen.getByTestId("form-filters").textContent,
    );
    expect(roundTripped).toHaveLength(1);
    expect(roundTripped[0]).toMatchObject({
      property: "attributes",
      propertyId: "customer_tier",
      apiColType: "RESOURCE_ATTRIBUTE",
      filterConfig: {
        filterType: "text",
        filterOp: "in",
        filterValue: ["enterprise", "startup"],
      },
    });
  });
});
