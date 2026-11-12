// PF-7 (WR-13 / WR-14 product risk): "Copy from another dataset" in a
// launch build creates the copy through dev's add-as-new with exactly
// {name, dataset_id, columns}, and never asks the DSX-only imports preview
// (absentPathGuard would refuse it). The prototype keeps its preview.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { useController } from "react-hook-form";
import PropTypes from "prop-types";
import { afterEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { IMPORTS_PATH } from "src/sections/common/AddData/mappingModel";
import ExistingDatasetModal from "../ExistingDatasetModal";

const h = vi.hoisted(() => ({
  post: null,
  // Stable references: the modal's effect keys on the columns array.
  datasets: [
    { datasetId: "seed-1", name: "Seed" },
    { datasetId: "upload-1", name: "Uploaded" },
  ],
  columns: [
    { id: "col-q", name: "question", data_type: "text" },
    { id: "col-a", name: "answer", data_type: "text" },
  ],
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  const pending = () => new Promise(() => {});
  return {
    ...real,
    default: {
      ...real.default,
      get: pending,
      post: (...a) => h.post(...a),
    },
  };
});
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: h.datasets }),
  useGetDatasetColumns: (dataset, options) => ({
    data: options?.enabled && dataset === "seed-1" ? h.columns : undefined,
    isLoading: false,
  }),
}));
// The searchable picker, as a plain select on the same form field.
vi.mock("src/components/FromSearchSelectField", () => {
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
  SelectStub.propTypes = {
    control: PropTypes.object,
    fieldName: PropTypes.string,
    options: PropTypes.array,
    label: PropTypes.string,
  };
  return { FormSearchSelectFieldControl: SelectStub };
});
vi.mock("src/sections/develop-detail/Common/FieldSelection", () => ({
  default: () => null,
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

function Landed() {
  const { dataset } = useParams();
  return <div data-testid="landed">{dataset}</div>;
}

const renderModal = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={["/dashboard/develop"]}>
          <Routes>
            <Route
              path="/dashboard/develop"
              element={
                <ExistingDatasetModal
                  open
                  onClose={() => {}}
                  refreshGrid={() => {}}
                  closeDrawer={() => {}}
                />
              }
            />
            <Route path="/dashboard/develop/:dataset" element={<Landed />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
};

const fillAndChoose = () => {
  fireEvent.change(screen.getByPlaceholderText("Enter dataset Name"), {
    target: { value: "copy-of-seed" },
  });
  fireEvent.change(screen.getByLabelText("Choose a dataset"), {
    target: { value: "seed-1" },
  });
};
const urls = () => h.post.mock.calls.map((c) => String(c[0]));
const settle = () => new Promise((r) => setTimeout(r, 80));

afterEach(() => {
  setLaunchOverrideForTest(null);
  h.post = null;
});

describe("Copy from another dataset (PF-7)", () => {
  it("a launch build creates the copy through add-as-new with exactly {name, dataset_id, columns} and sends no imports preview", async () => {
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
    h.post = vi.fn(async () => ({
      data: { status: true, result: { dataset_id: "new-1" } },
    }));
    renderModal();
    fillAndChoose();
    await settle();
    // Choosing and naming alone sends nothing.
    expect(urls()).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
    const [url, body] = h.post.mock.calls[0];
    expect(url).toMatch(/\/model-hub\/develops\/add-as-new\/$/);
    expect(Object.keys(body).sort()).toEqual(["columns", "dataset_id", "name"]);
    expect(body).toEqual({
      name: "copy-of-seed",
      dataset_id: "seed-1",
      columns: { "col-q": "question", "col-a": "answer" },
    });
    // It lands on the new dataset's Data tab.
    expect((await screen.findByTestId("landed")).textContent).toBe("new-1");
    expect(urls().some((u) => u.includes(IMPORTS_PATH))).toBe(false);
  });

  it("the prototype keeps its preview of the copy (positive control)", async () => {
    setLaunchOverrideForTest({ launch: false, experimentsBasic: false });
    h.post = vi.fn(() => new Promise(() => {}));
    renderModal();
    fillAndChoose();
    await waitFor(() =>
      expect(urls().some((u) => u.includes(IMPORTS_PATH))).toBe(true),
    );
    const [, body] = h.post.mock.calls.find((c) =>
      String(c[0]).includes(IMPORTS_PATH),
    );
    expect(body).toMatchObject({
      op: "preview",
      source: { kind: "dataset", dataset_id: "seed-1" },
    });
  });
});
