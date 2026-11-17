// Restore row 7: "Add data using SDK" is back, first, in Datasets › New
// dataset and in Dataset › Add rows (production 24cc6576 lists it first in
// both), and it opens production's SDK dialogs. Real drawers and real SDK
// dialogs; only the transport, the code editor and unrelated doors are mocked.
import React, { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  getDefaultNormalizer,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { palette } from "src/theme/palette";
import { endpoints } from "src/utils/axios";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import AddDatasetDrawer from "../AddDatasetDrawer/AddDatasetDrawer";
import AddRowDrawer from "../AddRowDrawer/AddRowDrawer";

const h = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  close: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: {
    post: (...args) => h.post(...args),
    get: (...args) => h.get(...args),
  },
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [] }),
}));
vi.mock("src/sections/common/ux/toast", () => ({
  notify: () => ({ undone: Promise.resolve(false) }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: h.refresh }),
}));
vi.mock("src/sections/develop-detail/DataTab/Versions/changeSetStore", () => ({
  useChangeSet: () => ({ addOp: vi.fn(), revert: vi.fn() }),
  useChangeSetStore: {},
}));
vi.mock(
  "src/sections/develop-detail/DataTab/DatapointDrawerV2/useRowViewUrl",
  () => ({
    default: () => ({ openRow: vi.fn() }),
  }),
);
vi.mock("src/sections/common/AddData/GenerateRowsDialog", () => ({
  useGenerateRows: {},
}));
// The code editor: a plain read-only textarea holding the snippet text.
vi.mock("@monaco-editor/react", async () => {
  const { default: types } = await import("prop-types");
  const Editor = ({ value }) => (
    <textarea aria-label="Code" value={value ?? ""} readOnly />
  );
  Editor.propTypes = { value: types.string };
  return { Editor };
});
// Unrelated doors' dialogs.
vi.mock("../AddDatasetDrawer/UploadFileModal", () => ({ default: () => null }));
vi.mock("../AddDatasetDrawer/ManuallyCreateDataset", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/TaskSuite/NewTaskSuiteDialog", () => ({
  default: () => null,
}));
vi.mock("../AddRowDrawer/UploadFileModal", () => ({ default: () => null }));
vi.mock("../AddRowDrawer/ExistingDatasetModal", () => ({
  default: () => null,
}));
vi.mock("../AddRowDrawer/SetEmptyRow", () => ({ default: () => null }));
vi.mock("../AddRowDrawer/PasteRowsDialog", () => ({ default: () => null }));
vi.mock("../AddRowDrawer/CreateSyntheticData", () => ({ default: () => null }));

const DATASET = "11111111-1111-4111-8111-111111111111";
const NEW_DATASET = "22222222-2222-4222-8222-222222222222";
const CODE = {
  python_add_col:
    "from fi.datasets import Dataset\ndataset = dataset.add_columns(columns=columns)",
  python_add_row:
    "from fi.datasets import Dataset\ndataset = dataset.add_rows(rows=rows)",
  typescript_add_col: 'import { Dataset } from "@future-agi/sdk";',
  typescript_add_row: 'import { Dataset } from "@future-agi/sdk";',
  curl_add_col: "curl --request POST",
  curl_add_row: "curl --request POST",
};
const LAUNCH = {
  launch: true,
  experimentsBasic: false,
  madeReal: madeRealSurfaceIds(),
};
const BUILDS = [
  ["a launch build", LAUNCH],
  ["a prototype or test build", { launch: false, experimentsBasic: false }],
];
const theme = createTheme({ palette: palette("light") });
const clients = [];

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}
function Sheet() {
  const [open, setOpen] = useState(true);
  return (
    <AddRowDrawer
      open={open}
      onClose={() => {
        h.close();
        setOpen(false);
      }}
    />
  );
}
function mount(routes, entry) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[entry]}>
          <LocationProbe />
          <Routes>{routes}</Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const mountNewDataset = () =>
  mount(
    [
      <Route
        key="list"
        path="/dashboard/develop"
        element={
          <AddDatasetDrawer open onClose={h.close} refreshGrid={h.refresh} />
        }
      />,
      <Route
        key="dataset"
        path="/dashboard/develop/:dataset"
        element={<div>Dataset page</div>}
      />,
    ],
    "/dashboard/develop",
  );
const mountAddRows = () =>
  mount(
    <Route path="/dashboard/develop/:dataset" element={<Sheet />} />,
    `/dashboard/develop/${DATASET}`,
  );

/** The SDK door, listed first among the drawer's doors. */
function sdkDoor() {
  const door = screen.getByRole("button", { name: /^Add data using SDK/ });
  expect(door).toBeVisible();
  expect(door).not.toHaveAttribute("aria-disabled", "true");
  expect(door.parentElement.firstElementChild).toBe(door);
  return door;
}
const clickText = (text) =>
  fireEvent.click(screen.getByText(text).closest("button"));
// The snippets span lines. Testing Library collapses the field's whitespace
// by default but compares a string matcher as written, so match exactly.
const findCode = (code) =>
  screen.findByDisplayValue(code, {
    normalizer: getDefaultNormalizer({ trim: false, collapseWhitespace: false }),
  });

beforeEach(() => {
  setLaunchOverrideForTest(LAUNCH);
  h.close.mockReset();
  h.refresh.mockReset();
  h.get.mockReset().mockRejectedValue(new Error("Unexpected GET"));
  h.post.mockReset().mockImplementation((url, body) => {
    if (url === endpoints.develop.createEmptyDataset)
      return Promise.resolve({
        data: {
          result: { dataset_id: NEW_DATASET, dataset_name: "SDK rows" },
        },
      });
    if (url === endpoints.row.addRowSdk)
      return Promise.resolve({
        data: {
          result: {
            dataset: { id: body?.dataset_id ?? NEW_DATASET, name: "SDK rows" },
            code: CODE,
          },
        },
      });
    return Promise.reject(new Error(`Unexpected POST ${url}`));
  });
  const noNetwork = () => {
    throw new Error("Network is forbidden in this test");
  };
  vi.stubGlobal("fetch", vi.fn(noNetwork));
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(noNetwork);
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("restore row 7: Add data using SDK", () => {
  it.each(BUILDS)("New dataset lists it first in %s", (_name, state) => {
    setLaunchOverrideForTest(state);
    mountNewDataset();
    expect(screen.getByRole("heading", { name: "New dataset" })).toBeVisible();
    within(sdkDoor()).getByText("Add SDK to import your data to our system");
    expect(h.post).not.toHaveBeenCalled();
  });

  it.each(BUILDS)("Add rows lists it first in %s", (_name, state) => {
    setLaunchOverrideForTest(state);
    mountAddRows();
    within(sdkDoor()).getByText("Add SDK to import your data to our system");
    expect(h.post).not.toHaveBeenCalled();
  });

  it("New dataset opens the SDK dialog: name, create, then the dataset's code", async () => {
    mountNewDataset();
    fireEvent.click(sdkDoor());
    const dialog = await screen.findByRole("dialog", {
      name: /Create Dataset/,
    });
    fireEvent.change(
      within(dialog).getByPlaceholderText("Enter dataset name"),
      { target: { value: "SDK rows" } },
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(h.post).toHaveBeenCalledWith(
        endpoints.develop.createEmptyDataset,
        {
          new_dataset_name: "SDK rows",
          model_type: "GenerativeLLM",
          is_sdk: true,
        },
        { headers: { "Content-Type": "multipart/form-data" } },
      ),
    );
    await waitFor(() =>
      expect(h.post).toHaveBeenCalledWith(
        endpoints.row.addRowSdk,
        { dataset_name: "SDK rows" },
        {},
      ),
    );
    expect(await screen.findByText("Add Rows via SDK or API")).toBeVisible();
    expect(await findCode(CODE.python_add_col)).toBeInTheDocument();
    expect(screen.getByText(NEW_DATASET)).toBeInTheDocument();
    clickText("Done");
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        `/dashboard/develop/${NEW_DATASET}?tab=data`,
      ),
    );
  });

  it("New dataset: Cancel in the SDK dialog returns to the chooser without a request", async () => {
    mountNewDataset();
    fireEvent.click(sdkDoor());
    const dialog = await screen.findByRole("dialog", {
      name: /Create Dataset/,
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /^Add data using SDK/ }),
      ).toBeVisible(),
    );
    expect(h.close).not.toHaveBeenCalled();
    expect(h.post).not.toHaveBeenCalled();
  });

  it("Add rows opens the SDK dialog with this dataset's code; Done closes it and the sheet", async () => {
    mountAddRows();
    fireEvent.click(sdkDoor());
    expect(await screen.findByText("Add Rows via SDK or API")).toBeVisible();
    await waitFor(() =>
      expect(h.post).toHaveBeenCalledWith(endpoints.row.addRowSdk, {
        dataset_id: DATASET,
      }),
    );
    expect(await findCode(CODE.python_add_row)).toBeInTheDocument();
    expect(screen.getByText(DATASET)).toBeInTheDocument();
    clickText("Done");
    expect(h.close).toHaveBeenCalledOnce();
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/develop/${DATASET}`,
    );
  });
});
