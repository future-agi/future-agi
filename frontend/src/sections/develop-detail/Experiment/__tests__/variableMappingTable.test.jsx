// IT-R4 G1 P1: a variable with no column never blocks. The Data step's
// mapping table offers Suggested, Columns, Leave empty and Same value.
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import VariableMappingTable from "../VariableMappingTable";
import { ExperimentFormContext } from "../ExperimentFormContext";

const COLUMNS = [
  { id: "c1", name: "instruction", data_type: "text" },
  { id: "c2", name: "metadata", data_type: "json" },
];
const SCHEMAS = {
  c2: {
    paths: [
      {
        rel: "$.customer.tier",
        path: "metadata.customer.tier",
        kinds: { string: 30 },
        coverage: { present: 30, total: 30 },
      },
      {
        rel: "$.order.id",
        path: "metadata.order.id",
        kinds: { string: 30 },
        coverage: { present: 30, total: 30 },
      },
    ],
  },
};

let latest = null;
function Harness({ mapping }) {
  const form = useForm({
    defaultValues: {
      promptConfig: [
        {
          variables: ["instruction", "customer_tier"],
          variableMapping: mapping,
        },
      ],
    },
  });
  latest = form;
  return (
    <MemoryRouter>
      <ExperimentFormContext.Provider
        value={{
          mappingColumns: COLUMNS,
          jsonSchemas: SCHEMAS,
          sampleRow: { row_id: "r1", c1: { cell_value: "Cancel order" } },
          sampleRowLabel: "Row 1",
        }}
      >
        <VariableMappingTable
          control={form.control}
          setValue={form.setValue}
          fieldPrefix="promptConfig.0"
          title="Support Agent v3"
        />
      </ExperimentFormContext.Provider>
    </MemoryRouter>
  );
}
Harness.propTypes = { mapping: PropTypes.object };

afterEach(cleanup);

describe("VariableMappingTable · unmapped variable", () => {
  it("says what to do and offers Leave empty / Same value", () => {
    render(<Harness mapping={{ instruction: "c1", customer_tier: "" }} />);
    expect(screen.getByTestId("unmapped-message").textContent).toContain(
      "{{customer_tier}} has no column. Pick a column, or leave it empty.",
    );
    const combo = within(
      screen.getByTestId("column-select-customer_tier"),
    ).getByRole("combobox");
    fireEvent.mouseDown(combo);
    const list = screen.getByRole("listbox");
    expect(within(list).getByText("Suggested")).toBeTruthy();
    expect(
      within(screen.getByTestId("suggested-customer_tier")).getByText(
        "metadata.customer.tier",
      ),
    ).toBeTruthy();
    expect(screen.getByTestId("suggested-customer_tier").textContent).toContain(
      "in 30 of 30 rows",
    );
    expect(within(list).getByText("Nested paths (2) ▸")).toBeTruthy();
    expect(within(list).getByText("Same value for every row…")).toBeTruthy();
    fireEvent.click(screen.getByTestId("leave-empty-customer_tier"));
    expect(
      latest.getValues("promptConfig.0.variableMapping.customer_tier"),
    ).toEqual({ kind: "empty" });
    expect(screen.queryByTestId("unmapped-message")).toBeNull();
    expect(screen.getByTestId("empty-label-customer_tier").textContent).toBe(
      "Empty on every row",
    );
  });

  it("reads a constant as the row's value", () => {
    render(
      <Harness
        mapping={{
          instruction: "c1",
          customer_tier: { kind: "constant", value: "enterprise" },
        }}
      />,
    );
    expect(screen.getByTestId("constant-label-customer_tier").textContent).toBe(
      '"enterprise" · same for every row',
    );
    expect(screen.getByTestId("mapping-chip").textContent).toBe("All mapped");
  });
});
