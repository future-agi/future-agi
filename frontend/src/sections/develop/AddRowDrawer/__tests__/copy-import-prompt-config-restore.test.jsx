/* eslint-disable react/prop-types */
// Restore row 6: Dataset > Add rows > Copy from another dataset offers both
// production choices, "Import Data" and "Import data and prompt
// configuration" (production's default). The second asks the columns read
// for the source's Run Prompt columns (include_prompt=true), and the copy
// then carries those columns' values like any other mapped column.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import ExistingDatasetModal from "../ExistingDatasetModal";

const h = vi.hoisted(() => ({
  get: null,
  post: null,
  datasets: [
    { datasetId: "target-1", name: "Target" },
    { datasetId: "seed-1", name: "Seed" },
  ],
  sourceColumns: [
    { id: "col-q", name: "question", data_type: "text" },
    { id: "col-a", name: "answer", data_type: "text" },
  ],
  // A Run Prompt column: the server lists it only with include_prompt.
  promptColumn: { id: "col-rp", name: "summary", data_type: "text" },
  targetColumns: [
    { id: "tgt-q", name: "question", data_type: "text" },
    { id: "tgt-a", name: "answer", data_type: "text" },
    { id: "tgt-summary", name: "summary", data_type: "text" },
  ],
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    default: {
      ...real.default,
      get: (...a) => h.get(...a),
      post: (...a) => h.post(...a),
    },
  };
});
// The real columns hook (it is what sends include_prompt); only the dataset
// list is fixed.
vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useDevelopDatasetList: () => ({ data: h.datasets }),
}));
// The searchable picker, as a plain select on the same form field.
vi.mock("src/components/FromSearchSelectField", async () => {
  const { useController } = await import("react-hook-form");
  function SelectStub({ control, fieldName, options, label }) {
    const { field } = useController({ control, name: fieldName });
    return (
      <select
        aria-label={label}
        value={field.value ?? ""}
        onChange={(e) => field.onChange(e.target.value)}
      >
        <option value="">Select Option</option>
        {(options ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  return { FormSearchSelectFieldControl: SelectStub };
});
// One line per source column the modal offers to copy.
vi.mock("src/sections/develop-detail/Common/FieldSelection", () => ({
  default: ({ field }) => <div data-testid="copy-source-field">{field}</div>,
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: () => {},
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: () => {},
}));

const theme = createTheme({ palette: palette("light") });

const COLUMNS_URL = (id) => new RegExp(`/model-hub/dataset/columns/${id}/$`);

const serverGet = async (url, config) => {
  const u = String(url);
  if (COLUMNS_URL("target-1").test(u)) {
    return { data: { result: { columns: h.targetColumns } } };
  }
  if (COLUMNS_URL("seed-1").test(u)) {
    const columns =
      config?.params?.include_prompt === true
        ? [...h.sourceColumns, h.promptColumn]
        : h.sourceColumns;
    return { data: { result: { columns } } };
  }
  return new Promise(() => {});
};

const renderModal = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={["/dashboard/develop/target-1"]}>
          <ExistingDatasetModal
            open
            datasetId="target-1"
            onClose={() => {}}
            refreshGrid={() => {}}
            closeDrawer={() => {}}
          />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
};

const seedReads = () =>
  h.get.mock.calls.filter(([url]) => COLUMNS_URL("seed-1").test(String(url)));
const shownFields = () =>
  screen.queryAllByTestId("copy-source-field").map((n) => n.textContent);

afterEach(() => {
  h.get = null;
  h.post = null;
});

describe("Copy from another dataset: Import data and prompt configuration (restore row 6)", () => {
  it("offers both choices, and the prompt-configuration choice reads and copies the source's Run Prompt columns", async () => {
    h.get = vi.fn(serverGet);
    h.post = vi.fn(async () => ({ data: { status: true, result: {} } }));
    renderModal();

    const importData = screen.getByRole("radio", { name: "Import Data" });
    const withPrompt = screen.getByRole("radio", {
      name: "Import data and prompt configuration",
    });
    // Production's default.
    expect(withPrompt).toBeChecked();
    expect(importData).not.toBeChecked();

    // "Import Data" first: the source's columns are read without prompts.
    fireEvent.click(importData);
    expect(importData).toBeChecked();
    fireEvent.change(screen.getByLabelText("Choose a dataset"), {
      target: { value: "seed-1" },
    });
    await waitFor(() => expect(seedReads()).toHaveLength(1));
    expect(seedReads()[0][1]).toEqual({ params: { include_prompt: false } });
    await waitFor(() => expect(shownFields()).toEqual(["question", "answer"]));

    // Picking the second choice asks for the Run Prompt columns too.
    fireEvent.click(withPrompt);
    expect(withPrompt).toBeChecked();
    await waitFor(() => expect(seedReads()).toHaveLength(2));
    expect(seedReads()[1][1]).toEqual({ params: { include_prompt: true } });
    await waitFor(() =>
      expect(shownFields()).toEqual(["question", "answer", "summary"]),
    );

    // Add copies the Run Prompt column's values into the same-named column.
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
    const [url, body] = h.post.mock.calls[0];
    expect(String(url)).toMatch(
      /\/model-hub\/develops\/target-1\/add_rows_from_existing_dataset\/$/,
    );
    expect(body).toEqual({
      source_dataset_id: "seed-1",
      column_mapping: {
        "col-q": "tgt-q",
        "col-a": "tgt-a",
        "col-rp": "tgt-summary",
      },
    });
  });
});
