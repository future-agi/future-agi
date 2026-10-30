/* eslint-disable react/prop-types */
import React, { useState } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";

const panel = vi.hoisted(() => ({ props: null }));
const picker = vi.hoisted(() => ({ props: null }));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: { project: {} },
}));
// The panel itself is the views'; here it records how it was configured and
// applies rows as a pick in it would.
vi.mock("../LLMTracing/TraceFilterPanel", () => ({
  default: (props) => {
    panel.props = props;
    return (
      <div data-testid="filter-panel">
        <button
          type="button"
          onClick={() =>
            props.onApply([
              ...(props.currentFilters || []),
              {
                field: "total_cost",
                fieldName: "Total Cost",
                fieldCategory: "system",
                fieldType: "number",
                apiColType: "SYSTEM_METRIC",
                operator: "greater_than",
                value: "2",
              },
            ])
          }
        >
          Pick cost over 2
        </button>
        <button type="button" onClick={props.onClose}>
          Close panel
        </button>
      </div>
    );
  },
}));

// The custom range picker picks days and hands back midnights.
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: (props) => {
    picker.props = props;
    return null;
  },
}));

import ScopeFilterBar from "../ScopeFilterBar";
import { OBSERVE_FILTER_CONFIG } from "../observeFilterConfig";
import {
  defaultScope,
  makeScope,
  presetWindow,
  withFilters,
} from "../observeScope";

const theme = createTheme({ palette: palette("light") });
const STATUS = {
  column_id: "status",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "ERROR",
    col_type: "SYSTEM_METRIC",
  },
};

function Harness({ unit, initial, onChange }) {
  const [scope, setScope] = useState(initial);
  return (
    <ThemeProvider theme={theme}>
      <ScopeFilterBar
        projectId="p1"
        unit={unit}
        scope={scope}
        onChange={(next) => {
          onChange(next);
          setScope(next);
        }}
      />
      <output data-testid="label">{scope.label}</output>
    </ThemeProvider>
  );
}

const renderBar = (unit, initial = defaultScope(unit)) => {
  const onChange = vi.fn();
  render(<Harness unit={unit} initial={initial} onChange={onChange} />);
  return onChange;
};

beforeEach(() => {
  panel.props = null;
  picker.props = null;
});

describe("the scope bar", () => {
  it.each(["traces", "spans", "sessions", "users"])(
    "opens the %s view's own filter panel",
    (unit) => {
      renderBar(unit);
      // Nothing reads the panel's catalog until it is opened.
      expect(screen.queryByTestId("filter-panel")).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Filter" }));
      expect(screen.getByTestId("filter-panel")).toBeInTheDocument();
      const config = OBSERVE_FILTER_CONFIG[unit];
      expect(panel.props).toMatchObject({
        open: true,
        projectId: "p1",
        source: config.source,
        propertyNamespace: config.propertyNamespace,
        tab: config.tab,
        attributeSource: config.attributeSource,
        isSpansView: config.isSpansView,
        filterFields: config.filterFields,
      });
      fireEvent.click(screen.getByRole("button", { name: "Close panel" }));
      expect(screen.queryByTestId("filter-panel")).toBeNull();
    },
  );

  it("adds a filter from the panel and relabels the scope", () => {
    const onChange = renderBar("sessions");
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));
    fireEvent.click(screen.getByRole("button", { name: "Pick cost over 2" }));
    const next = onChange.mock.lastCall[0];
    expect(next.filters).toEqual([
      expect.objectContaining({
        column_id: "total_cost",
        filter_config: expect.objectContaining({
          filter_op: "greater_than",
          filter_value: 2,
        }),
      }),
    ]);
    expect(next.label).toBe("Past 7D · 1 filter");
    // The chip names the Sessions field.
    expect(screen.getByText("Total Cost")).toBeInTheDocument();
  });

  it("edits a chip in the panel, removes one and clears them all", () => {
    const initial = withFilters(defaultScope("traces"), [STATUS, STATUS]);
    const onChange = renderBar("traces", initial);
    const chips = screen.getAllByText("Status");
    expect(chips).toHaveLength(2);
    fireEvent.click(chips[0]);
    // The panel opens on the scope's filters as rows.
    expect(panel.props.currentFilters).toEqual([
      expect.objectContaining({ field: "status", value: ["ERROR"] }),
      expect.objectContaining({ field: "status", value: ["ERROR"] }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Close panel" }));

    const [first] = document.querySelectorAll("[data-filter-chip-column]");
    fireEvent.click(within(first).getByTestId("CancelIcon"));
    expect(onChange.mock.lastCall[0].filters).toEqual([STATUS]);
    expect(screen.getByTestId("label")).toHaveTextContent("Past 7D · 1 filter");

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onChange.mock.lastCall[0].filters).toEqual([]);
    expect(screen.getByTestId("label")).toHaveTextContent(/^Past 7D$/);
  });

  it("offers every Observe date preset and keeps the filters", () => {
    const initial = withFilters(defaultScope("users"), [STATUS]);
    const onChange = renderBar("users", initial);
    fireEvent.click(screen.getByRole("button", { name: "Past 7D" }));
    const options = screen.getAllByRole("menuitem");
    expect(options.map((item) => item.textContent)).toEqual([
      "Today",
      "Yesterday",
      "Past 7D",
      "Past 30D",
      "Past 3M",
      "Past 6M",
      "Past 12M",
      "Custom range",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Past 30D" }));
    const next = onChange.mock.lastCall[0];
    expect(next).toMatchObject({
      unit: "users",
      dateOption: "30D",
      filters: [STATUS],
      label: "Past 30D · 1 filter",
    });
    expect(next.window).toEqual(presetWindow("30D"));
  });

  it("searches users as the Users view does, and only users", () => {
    const initial = { ...defaultScope("users"), search: "ada" };
    const onChange = renderBar("users", initial);
    const box = screen.getByRole("textbox", { name: "Search users" });
    expect(box).toHaveValue("ada");
    fireEvent.change(box, { target: { value: "acme " } });
    // The box keeps the space being typed; the scope keeps the words.
    expect(box).toHaveValue("acme ");
    expect(onChange.mock.lastCall[0]).toMatchObject({
      search: "acme",
      label: "Past 7D · “acme”",
    });
    fireEvent.change(box, { target: { value: "" } });
    expect(onChange.mock.lastCall[0].search).toBeUndefined();
    expect(onChange.mock.lastCall[0].label).toBe("Past 7D");
  });

  it("reads both days of a custom range picked as 22-23 Oct, and says so", () => {
    const onChange = renderBar("sessions");
    fireEvent.click(screen.getByRole("button", { name: "Past 7D" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Custom range" }));
    act(() =>
      picker.props.setDateFilter([
        "2026-10-22 00:00:00",
        "2026-10-23 00:00:00",
      ]),
    );
    const next = onChange.mock.lastCall[0];
    expect(next.dateOption).toBe("Custom");
    expect(next.window).toEqual([
      new Date(2026, 9, 22).toISOString(),
      new Date(2026, 9, 23, 23, 59, 59, 999).toISOString(),
    ]);
    // The pill names the days the count reads.
    expect(
      screen.getByRole("button", { name: /Oct 22.23/ }),
    ).toBeInTheDocument();
  });

  it("labels a handed-over window ending at midnight by the days it reads", () => {
    renderBar(
      "sessions",
      makeScope({
        unit: "sessions",
        dateOption: "Custom",
        window: [new Date(2026, 9, 22), new Date(2026, 9, 23)],
      }),
    );
    expect(screen.getByRole("button", { name: /^Oct 22/ })).toHaveTextContent(
      /Oct 22$/,
    );
  });

  it("has no search for other units", () => {
    renderBar("sessions");
    expect(screen.queryByRole("textbox", { name: "Search users" })).toBeNull();
  });
});
