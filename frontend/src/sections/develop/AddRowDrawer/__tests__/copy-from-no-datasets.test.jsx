// FR-DATASETS-002: "Copy from another dataset" must not be a dead end for a
// new user. With no other dataset the pane says so and Add is disabled with
// that reason; with datasets but none chosen, Add shows an inline error
// instead of failing validation silently. The real picker renders here, so
// the error is the text a user sees, not a prop on a stub.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import ExistingDatasetModal from "../ExistingDatasetModal";

const NO_SOURCE =
  "No other datasets to copy from. Upload a file or start a blank grid first.";
const CHOOSE_ERROR = "Choose a dataset to copy from";

const h = vi.hoisted(() => ({
  post: null,
  // Set per test; one stable reference across renders.
  datasets: [],
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
  useGetDatasetColumns: () => ({ data: undefined, isLoading: false }),
}));
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

const renderModal = ({ datasets, datasetId }) => {
  h.datasets = datasets;
  h.post = vi.fn(() => new Promise(() => {}));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={["/dashboard/develop"]}>
          <ExistingDatasetModal
            open
            onClose={() => {}}
            refreshGrid={() => {}}
            datasetId={datasetId}
            closeDrawer={() => {}}
          />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
};

const addButton = () => screen.getByRole("button", { name: "Add" });
const settle = () => new Promise((r) => setTimeout(r, 50));

afterEach(() => {
  h.post = null;
  h.datasets = [];
});

describe("Copy from another dataset with nothing chosen (FR-DATASETS-002)", () => {
  it("FR-DATASETS-002-1: an empty workspace says there is nothing to copy and disables Add with that reason", async () => {
    renderModal({ datasets: [], datasetId: null });

    expect(screen.getByText(NO_SOURCE)).toBeInTheDocument();
    expect(screen.queryByText("Choose Dataset")).toBeNull();
    expect(addButton()).toBeDisabled();
    expect(addButton()).toHaveAccessibleDescription(NO_SOURCE);
    // Nothing to pick, so the picker does not open onto an empty menu.
    expect(screen.getByPlaceholderText("Select Option")).toBeDisabled();
    await settle();
    expect(h.post).not.toHaveBeenCalled();
  });

  it("FR-DATASETS-002-2: adding rows to the only dataset (itself filtered out) shows the same reason and disables Add", async () => {
    renderModal({
      datasets: [{ datasetId: "cur", name: "Cur" }],
      datasetId: "cur",
    });

    expect(screen.getByText(NO_SOURCE)).toBeInTheDocument();
    expect(addButton()).toBeDisabled();
    expect(addButton()).toHaveAccessibleDescription(NO_SOURCE);
    await settle();
    expect(h.post).not.toHaveBeenCalled();
  });

  it("FR-DATASETS-002-3: with a dataset to copy but none chosen, Add shows an inline error and sends nothing", async () => {
    renderModal({
      datasets: [{ datasetId: "a", name: "A" }],
      datasetId: null,
    });

    expect(screen.queryByText(NO_SOURCE)).toBeNull();
    expect(addButton()).toBeEnabled();
    expect(screen.queryByText(CHOOSE_ERROR)).toBeNull();

    fireEvent.click(addButton());

    expect(await screen.findByText(CHOOSE_ERROR)).toBeInTheDocument();
    await settle();
    expect(h.post).not.toHaveBeenCalled();
  });
});
