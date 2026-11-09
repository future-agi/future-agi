// Edit Column Type previews the conversion before enabling Confirm.
// The confirmation uses the sealed current-dataset disclosure; requests and
// the server-provided success toast remain unchanged.
import React from "react";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Controller } from "react-hook-form";
import { palette } from "src/theme/palette";

const put = vi.fn();
const snack = vi.fn();
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, put } };
});
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: snack }));
vi.mock("src/utils/Mixpanel", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, trackEvent: () => {} };
});
vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: () => {} }),
}));
// A plain select stands in for the styled one (same form field).
/* eslint-disable react/prop-types -- a test stand-in */
vi.mock("src/components/FormSelectField", () => ({
  FormSelectField: ({ control, fieldName, options, label }) => (
    <Controller
      control={control}
      name={fieldName}
      render={({ field }) => (
        <select
          aria-label={label}
          value={field.value}
          onChange={(e) => field.onChange(e.target.value)}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    />
  ),
}));
/* eslint-enable react/prop-types */

const theme = createTheme({ palette: palette("light") });
const COLUMN = { id: "col-1", name: "customer_message", dataType: "text" };
const PLAIN =
  "Changing this column to Document updates the current dataset and converts its values. Values that fail conversion are kept and marked with an error. This type change has no undo.";

let EditColumnType = null;
let useEditColumnTypeStore = null;
beforeAll(async () => {
  EditColumnType = (await import("../EditColumnType")).default;
  useEditColumnTypeStore = (await import("../../states"))
    .useEditColumnTypeStore;
}, 180000);

function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  useEditColumnTypeStore.setState({ editColumnType: COLUMN });
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={["/develop/ds-1"]}>
          <Routes>
            <Route path="/develop/:dataset" element={<EditColumnType />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const pickDocumentAndUpdate = async () => {
  const select = await screen.findByLabelText("New Column Type");
  await waitFor(() => expect(select.value).toBe("text"));
  fireEvent.change(select, { target: { value: "document" } });
  fireEvent.click(screen.getByRole("button", { name: "Update" }));
};

beforeEach(() => {
  put.mockReset();
  snack.mockReset();
});
afterEach(() => {
  cleanup();
  useEditColumnTypeStore?.setState({ editColumnType: null });
});

describe("Edit Column Type · the Force update confirm", () => {
  it("previews the values, then shows the sealed conversion disclosure", async () => {
    put.mockImplementation((url, body) =>
      Promise.resolve(
        body.preview
          ? {
              data: {
                status: true,
                result: { invalid_count: 250, new_data_type: "document" },
              },
            }
          : {
              data: {
                status: true,
                result: {
                  message:
                    "Column type updated · 250 values that were not document links were emptied",
                },
              },
            },
      ),
    );
    renderDialog();
    await pickDocumentAndUpdate();

    expect(
      await screen.findByText("Change “customer_message” to Document?"),
    ).toBeTruthy();
    expect((await screen.findByText(PLAIN, { exact: true })).textContent).toBe(PLAIN);
    expect(screen.queryByText(/can cause loss of data/)).toBeNull();
    expect(put).toHaveBeenCalledTimes(1);
    expect(put.mock.calls[0][0]).toContain("/update_column_type/col-1/");
    expect(put.mock.calls[0][1]).toEqual({
      new_column_type: "document",
      preview: true,
      force_update: true,
    });

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    expect(put.mock.calls[1][1]).toEqual({
      new_column_type: "document",
      preview: false,
      force_update: true,
    });
    await waitFor(() =>
      expect(snack).toHaveBeenCalledWith(
        "Column type updated · 250 values that were not document links were emptied",
        { variant: "success" },
      ),
    );
  });

  it("Confirm waits for the count; a failed read still says what Force update does", async () => {
    let fail = null;
    put.mockImplementation(
      () =>
        new Promise((_, reject) => {
          fail = () => reject(new Error("network"));
        }),
    );
    renderDialog();
    await pickDocumentAndUpdate();
    expect(
      await screen.findByText("Checking which values fit Document…"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirm" }).disabled).toBe(true);
    fail();
    expect(
      await screen.findByText(
        `Couldn't check the values. ${PLAIN}`,
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirm" }).disabled).toBe(
      false,
    );
  });
});
