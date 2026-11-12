import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { palette } from "src/theme/palette";
import CreateSyntheticDataView from "./CreateSyntheticDataView";
import { resetEditSyntheticStates } from "./EditSyntheticData/state";

const h = vi.hoisted(() => ({ put: vi.fn(), post: vi.fn() }));
const DATASET = "11111111-1111-4111-8111-111111111111";
const UPDATE = `/model-hub/develops/${DATASET}/update-synthetic-config/`;
const CREATE = "/model-hub/develops/create-synthetic-dataset/";
const clients = [];

vi.mock("src/utils/axios", () => ({
  default: { put: (...args) => h.put(...args), post: (...args) => h.post(...args) },
  endpoints: {
    develop: {
      updateSyntheticDataset: (id) => `/model-hub/develops/${id}/update-synthetic-config/`,
      createSyntheticDataset: "/model-hub/develops/create-synthetic-dataset/",
    },
  },
}));
vi.mock("src/utils/Mixpanel", () => ({ trackEvent: vi.fn(), Events: {}, PropertyName: {} }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/knowledge-base/files", () => ({ useKnowledgeBaseList: () => ({ data: [] }) }));
vi.mock("src/hooks/useBeforeUnload", () => ({ useBeforeUnload: vi.fn() }));
vi.mock("src/sections/develop-detail/states", () => ({
  useDatasetOriginStore: () => ({ setProcessingComplete: vi.fn() }),
}));

// Keep the real editor submission, form state, option modal and mutation transport.
/* eslint-disable react/prop-types */
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/Common/PageHeadings", () => ({ default: () => null }));
vi.mock("./CreateSyntheticData/FormTabs", () => ({ default: () => null }));
vi.mock("./CreateSyntheticData/SummarySection", () => ({ default: () => null }));
vi.mock("./CreateSyntheticData/AddColumnForm", () => ({ default: () => null }));
vi.mock("./CreateSyntheticData/AddDescriptionForm", () => ({ default: () => null }));
vi.mock("./CreateSyntheticData/HelpCreateSyntheticData", () => ({ default: () => null }));
vi.mock("./CreateSyntheticData/DetailForm", async () => {
  const { useFormContext } = await import("react-hook-form");
  return {
    default: function TestDetails() {
      const { register } = useFormContext();
      return (
        <>
          <input aria-label="Rows to generate" type="number" {...register("rowNumber", { valueAsNumber: true })} />
          <button type="submit">Finish configuration</button>
        </>
      );
    },
  };
});
/* eslint-enable react/prop-types */

function mount(numRows = 20) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={createTheme({ palette: palette("light") })}>
        <MemoryRouter initialEntries={[`/dashboard/develop/${DATASET}`]}>
          <Routes>
            <Route path="/dashboard/develop/:dataset" element={
              <CreateSyntheticDataView editMode editData={{
                dataset: { name: "Saved synthetic data", description: "Support questions", objective: "Support", patterns: "Short" },
                num_rows: numRows,
                // Reopening an appended configuration must not make Replace append.
                generation_mode: "append_synthetic",
                columns: [{ name: "question", data_type: "text", description: "A question", property: {} }],
              }} />
            } />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

async function openOptions() {
  fireEvent.change(screen.getByRole("spinbutton", { name: "Rows to generate" }), { target: { value: "10" } });
  fireEvent.click(screen.getByRole("button", { name: "Finish configuration" }));
  await screen.findByRole("radio", { name: /Add it to existing dataset/ });
}

beforeEach(() => {
  vi.clearAllMocks();
  resetEditSyntheticStates();
  // Leave transport pending: this suite checks requests, not success navigation.
  h.put.mockImplementation(() => new Promise(() => {}));
  h.post.mockImplementation(() => new Promise(() => {}));
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  resetEditSyntheticStates();
});

describe("Edit Synthetic Data append choice", () => {
  it.each([10, 20])("sends an explicit append operation when the saved count is %s", async (numRows) => {
    mount(numRows);
    await openOptions();
    fireEvent.click(screen.getByRole("radio", { name: /Add it to existing dataset/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add", exact: true }));
    await waitFor(() => expect(h.put).toHaveBeenCalledTimes(1));
    expect(h.put).toHaveBeenCalledWith(UPDATE, expect.objectContaining({
      generation_mode: "append_synthetic",
      num_rows: 10,
      columns: [{ name: "question", data_type: "text", description: "A question", property: {} }],
    }));
    expect(h.put.mock.calls[0][1]).not.toHaveProperty("regenerate");
    expect(h.post).not.toHaveBeenCalled();
  });

  it("keeps replacement explicit and does not carry the saved append operation", async () => {
    mount();
    await openOptions();
    fireEvent.click(screen.getByRole("radio", { name: /Replace the current dataset/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add", exact: true }));
    await waitFor(() => expect(h.put).toHaveBeenCalledTimes(1));
    expect(h.put).toHaveBeenCalledWith(UPDATE, expect.objectContaining({ regenerate: true, num_rows: 10 }));
    expect(h.put.mock.calls[0][1]).not.toHaveProperty("generation_mode");
    expect(h.post).not.toHaveBeenCalled();
  });

  it("keeps create-as-new on POST without append or regeneration flags", async () => {
    mount();
    await openOptions();
    fireEvent.click(screen.getByRole("radio", { name: /Create as new dataset/ }));
    fireEvent.change(screen.getByRole("textbox", { name: /Dataset name/ }), { target: { value: "New dataset" } });
    fireEvent.click(screen.getByRole("button", { name: "Add", exact: true }));
    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
    expect(h.post).toHaveBeenCalledWith(CREATE, expect.objectContaining({
      dataset: expect.objectContaining({ name: "New dataset" }), num_rows: 10,
    }));
    expect(h.post.mock.calls[0][1]).not.toHaveProperty("generation_mode");
    expect(h.post.mock.calls[0][1]).not.toHaveProperty("regenerate");
    expect(h.put).not.toHaveBeenCalled();
  });
});
