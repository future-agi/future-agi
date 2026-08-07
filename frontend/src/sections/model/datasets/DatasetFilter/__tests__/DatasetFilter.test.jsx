/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, userEvent } from "src/utils/test-utils";
import DatasetFilter from "../DatasetFilter";
import DatasetFilterRow from "../DatasetFilterRow";

const baseFilterProps = {
  filterOpen: true,
  properties: [],
  filters: [],
  setFilters: () => {},
  datasetOptions: {},
  addFilter: () => {},
  removeFilter: () => {},
};

describe("DatasetFilter", () => {
  it("renders a labelled, clickable Add filter button", async () => {
    const addFilter = vi.fn();
    render(<DatasetFilter {...baseFilterProps} addFilter={addFilter} />);

    // The bug shipped an icon-only <Button /> with no children, so there was
    // no accessible "Add filter" control for a user (or this query) to reach.
    const button = screen.getByRole("button", { name: /add filter/i });

    await userEvent.setup().click(button);
    expect(addFilter).toHaveBeenCalledTimes(1);
  });
});

describe("DatasetFilterRow", () => {
  const properties = [
    { label: "Score", value: "score", dataType: "number" },
    { label: "Name", value: "name", dataType: "string" },
  ];

  const renderRow = (setValuesForIndex) =>
    render(
      <DatasetFilterRow
        properties={properties}
        setValuesForIndex={setValuesForIndex}
        filter={{ key: "", dataType: undefined, value: [], operator: "equal" }}
        idx={0}
        options={[]}
        removeFilter={() => {}}
      />,
    );

  it("propagates the picked property's dataType through onChange", () => {
    const setValuesForIndex = vi.fn();
    renderRow(setValuesForIndex);

    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(screen.getByRole("option", { name: "Score" }));

    expect(setValuesForIndex).toHaveBeenCalledWith(
      0,
      expect.objectContaining({
        key: "score",
        dataType: "number",
        operator: "equal",
      }),
    );
  });
});
