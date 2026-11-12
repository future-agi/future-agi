/* eslint-disable react/prop-types */
// Row 20 (restore): New dataset > Generate synthetic data > column properties.
// Production lets a user add their own property ("Add New Property"). The
// launch build removed the control. The generator that reads a column's
// properties is not in this build's open-source code, so the control comes
// back visible and disabled with the line "Not available in this release",
// and clicking it opens nothing.
import React from "react";
import { FormProvider, useForm } from "react-hook-form";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import PropertyFields from "../PropertyFields";

// PropertyFields' own imports, stubbed so only the component itself renders.
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: () => null,
}));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: () => null,
}));
vi.mock("../CategoricalFields", () => ({ default: () => null }));

const theme = createTheme({ palette: palette("light") });

// newProps MUST be one stable array. PropertyFields defaults it to `[]` (a new
// array every render) and runs useEffect([newProps]) -> setOptions, so leaving
// it out re-renders forever inside act() and hangs the worker. The real caller
// passes the column's array from state (CreateSyntheticDataView useState ->
// AddColumnForm newProps[index] -> AddColumnField), stable in the same way.
const NO_NEW_PROPS = [];
const DEFAULT_VALUES = {
  columns: [{ name: "question", data_type: "text", property: [] }],
};

const form = { current: null };

function Harness() {
  const methods = useForm({ defaultValues: DEFAULT_VALUES });
  form.current = methods;
  return (
    <ThemeProvider theme={theme}>
      <FormProvider {...methods}>
        <PropertyFields
          control={methods.control}
          nestIndex={0}
          newProps={NO_NEW_PROPS}
        />
      </FormProvider>
    </ThemeProvider>
  );
}

afterEach(() => {
  cleanup();
  form.current = null;
});

describe("PropertyFields: Add New Property (row 20)", () => {
  it("shows Add New Property, disabled, with the release line", () => {
    render(<Harness />);
    const button = screen.getByRole("button", { name: "Add New Property" });
    expect(button).toBeDisabled();
    const note = screen.getByText("Not available in this release");
    expect(button).toHaveAttribute("aria-describedby", note.id);
  });

  it("keeps Add more properties working next to it", () => {
    render(<Harness />);
    const addMore = screen.getByRole("button", { name: "Add more properties" });
    expect(addMore).toBeEnabled();
    fireEvent.click(addMore);
    expect(form.current.getValues("columns.0.property")).toHaveLength(1);
  });

  it("opens no dialog when the disabled control is clicked", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Add New Property" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
