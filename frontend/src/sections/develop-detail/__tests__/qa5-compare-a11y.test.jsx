import React, { useState } from "react";
import { expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import CompareDatasetDrawer from "../CompareDatasetDrawer";

const options = [
  { value: "a", label: "Current dataset" },
  { value: "b", label: "Product feedback" },
];
function Page() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Compare
      </button>
      <CompareDatasetDrawer
        compareDatasetDrawerVisible={open}
        onCompareDatasetDrawerClose={() => setOpen(false)}
        datasetOptions={options}
        setBaseColumnDrawerVisible={vi.fn()}
        onSelectedDatasetsChange={vi.fn()}
      />
    </>
  );
}

it("names dataset checkboxes, enters the drawer, and restores focus after Escape", async () => {
  const user = userEvent.setup();
  render(
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      <MemoryRouter initialEntries={["/datasets/a"]}>
        <Routes>
          <Route path="/datasets/:dataset" element={<Page />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
  const trigger = screen.getByRole("button", { name: "Compare" });
  await user.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Compare datasets" });
  expect(dialog.contains(document.activeElement)).toBe(true);
  const checkbox = screen.getByRole("checkbox", { name: "Product feedback" });
  checkbox.focus();
  await user.keyboard(" ");
  expect(checkbox).toBeChecked();
  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(trigger).toHaveFocus();
});
