// @vitest-environment jsdom
/* eslint-disable react/prop-types */
// W9 A2 (W9-SCOPE §5): the filter popover sends only complete conditions,
// shows an incomplete row's sentence, offers "Significant only" on Δ vs
// baseline; chips remove one condition or clear all and show the notes.
import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import ListFilterButton from "../ListFilterButton";
import FilterChips from "../FilterChips";
import { CONTRACT_TYPE } from "../listFilterModel";
import { DATASET_FIELDS } from "src/sections/develop/organize/datasetFields";

const F = (id) => DATASET_FIELDS.find((f) => f.id === id);
const cond = (column_id, filter_op, filter_value, extra = {}) => ({
  column_id,
  filter_config: {
    filter_type: CONTRACT_TYPE[F(column_id).type],
    filter_op,
    filter_value,
    ...extra,
  },
});

function Page({ initial = [], onChange }) {
  const [filters, setFilters] = useState(initial);
  const set = (f) => {
    setFilters(f);
    onChange?.(f);
  };
  return (
    <>
      <ListFilterButton
        title="Filter datasets"
        fields={DATASET_FIELDS}
        filters={filters}
        onChange={set}
        facets={{ tag: [{ value: "golden", label: "golden", count: 3 }] }}
      />
      <FilterChips
        filters={filters}
        fields={DATASET_FIELDS}
        onChange={set}
        notes={[
          "2 datasets have no finished run and are left out by “Last pass”.",
        ]}
      />
    </>
  );
}

describe("ListFilterButton", () => {
  it("adds a condition and applies it once it is complete", () => {
    const onChange = vi.fn();
    render(<Page onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));
    expect(
      screen.getByRole("dialog", { name: "Filter datasets" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add a condition" }));
    // Name contains (empty): its sentence shows, nothing is sent.
    expect(screen.getByText("“contains” needs a value for Name.")).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Name value"), {
      target: { value: "golden" },
    });
    expect(onChange).toHaveBeenLastCalledWith([
      cond("name", "contains", "golden"),
    ]);
    expect(screen.getByText("Name contains golden")).toBeTruthy();
  });

  it("reads a percent in % and sends a rate", () => {
    const onChange = vi.fn();
    render(
      <Page
        initial={[cond("last_pass", "less_than", 0.8)]}
        onChange={onChange}
      />,
    );
    expect(screen.getByText("Last pass < 80%")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Filter/ }));
    const input = screen.getByLabelText("Last pass value");
    expect(input.value).toBe("80");
    fireEvent.change(input, { target: { value: "75" } });
    expect(onChange).toHaveBeenLastCalledWith([
      cond("last_pass", "less_than", 0.75),
    ]);
  });

  it("offers Significant only on Δ vs baseline", () => {
    const onChange = vi.fn();
    render(
      <Page
        initial={[cond("delta_vs_baseline", "less_than", 0)]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Filter/ }));
    fireEvent.click(screen.getByLabelText("Significant only"));
    expect(onChange).toHaveBeenLastCalledWith([
      cond("delta_vs_baseline", "less_than", 0, { significant: true }),
    ]);
    expect(
      screen.getByText("Δ vs baseline < 0 pts (significant)"),
    ).toBeTruthy();
  });

  it("removes a condition and clears all", () => {
    const onChange = vi.fn();
    render(
      <Page
        initial={[
          cond("tag", "in", ["golden"]),
          cond("rows", "greater_than", 10),
        ]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Filter/ }));
    fireEvent.click(
      screen.getAllByRole("button", { name: "Remove condition" })[1],
    );
    expect(onChange).toHaveBeenLastCalledWith([cond("tag", "in", ["golden"])]);
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});

describe("FilterChips", () => {
  it("shows chips and notes; ✕ removes one; Clear filters removes all", () => {
    const onChange = vi.fn();
    render(
      <Page
        initial={[
          cond("tag", "in", ["golden"]),
          cond("last_pass", "less_than", 0.8),
        ]}
        onChange={onChange}
      />,
    );
    expect(
      screen.getAllByTestId("filter-chip").map((c) => c.textContent),
    ).toEqual(["Tag is golden✕", "Last pass < 80%✕"]);
    expect(screen.getByTestId("filter-note").textContent).toBe(
      "2 datasets have no finished run and are left out by “Last pass”.",
    );
    fireEvent.click(screen.getByLabelText("Remove Tag is golden"));
    expect(onChange).toHaveBeenLastCalledWith([
      cond("last_pass", "less_than", 0.8),
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(onChange).toHaveBeenLastCalledWith([]);
    expect(screen.queryByTestId("filter-chips")).toBeNull();
  });
});
