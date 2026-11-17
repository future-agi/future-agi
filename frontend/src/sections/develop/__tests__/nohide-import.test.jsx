// Import doors remain visible and follow the installed server capability.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import {
  MemoryRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { palette } from "src/theme/palette";
import { madeRealSurfaceIds } from "src/launch/madeReal";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import { apiPath } from "src/api/contracts/api-surface";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { launchRedirect } from "src/launch/launchRoutes";
import AddDatasetDrawer from "../AddDatasetDrawer/AddDatasetDrawer";
import AddRowDrawer from "../AddRowDrawer/AddRowDrawer";
import HuggingFacePage from "src/pages/dashboard/huggingface/HuggingFace";

const h = vi.hoisted(() => ({
  available: false,
  get: vi.fn(),
  post: vi.fn(),
  close: vi.fn(),
  refresh: vi.fn(),
  copyDialog: vi.fn(),
  uploadDialog: vi.fn(),
  emptyDialog: vi.fn(),
  addOp: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => h.get(...args),
    post: (...args) => h.post(...args),
  },
  endpoints: { develop: { createDatasetManually: "/manual-create" } },
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
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
  useChangeSet: () => ({ addOp: h.addOp, revert: vi.fn() }),
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
vi.mock("../AddDatasetDrawer/UploadFileModal", () => ({ default: () => null }));
vi.mock("../AddDatasetDrawer/V1PreviewLine", () => ({ default: () => null }));
vi.mock("src/sections/develop-detail/TaskSuite/NewTaskSuiteDialog", () => ({
  default: () => null,
}));
vi.mock("../AddRowDrawer/UploadFileModal", () => ({
  default: (props) => {
    h.uploadDialog(props);
    return null;
  },
}));
vi.mock("../AddRowDrawer/ExistingDatasetModal", () => ({
  default: (props) => {
    h.copyDialog(props);
    return null;
  },
}));
vi.mock("../AddRowDrawer/SetEmptyRow", () => ({
  default: (props) => {
    h.emptyDialog(props);
    return null;
  },
}));
vi.mock("../AddRowDrawer/AddSDKModal", () => ({ default: () => null }));
vi.mock("../AddRowDrawer/PasteRowsDialog", () => ({ default: () => null }));
vi.mock("../AddRowDrawer/CreateSyntheticData", () => ({ default: () => null }));
vi.mock("src/sections/huggingface/HuggingFaceDetailDrawer", () => ({
  default: () => null,
}));

const theme = createTheme({ palette: palette("light") });
const clients = [];
const NOTICE =
  "This install lacks the datasets package needed for Hugging Face import.";
function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}
function GuardedHuggingFace() {
  const redirect = launchRedirect(useLocation());
  return redirect ? <Navigate to={redirect.to} replace /> : <HuggingFacePage />;
}
/**
 * @param {React.ReactNode} node
 * @param {string | {pathname: string, state?: object}} [entry]
 * @param {boolean} [seedCapabilities]
 */
function mount(node, entry = "/dashboard/develop/d1", seedCapabilities = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, retryDelay: 0 } },
  });
  clients.push(client);
  if (seedCapabilities) {
    client.setQueryData(CAPABILITIES_QUERY_KEY, {
      data: {
        features:
          typeof h.available === "boolean"
            ? { huggingface_import: { allowed: h.available } }
            : {},
      },
    });
  }
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <HelmetProvider>
          <MemoryRouter initialEntries={[entry]}>
            <LocationProbe />
            <Routes>
              <Route path="/dashboard/develop/:dataset" element={node} />
              <Route path="/dashboard/huggingface" element={node} />
              <Route
                path="/dashboard/develop"
                element={<div>Datasets fallback</div>}
              />
            </Routes>
          </MemoryRouter>
        </HelmetProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  setLaunchOverrideForTest({
    launch: true,
    showUnreleased: false,
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
  h.available = false;
  h.get.mockReset().mockRejectedValue(new Error("Unexpected capability read"));
  h.close.mockReset();
  h.refresh.mockReset();
  h.copyDialog.mockClear();
  h.uploadDialog.mockClear();
  h.emptyDialog.mockClear();
  h.addOp.mockClear();
  h.post.mockReset().mockRejectedValue(new Error("Unexpected API call"));
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

describe.each(["create", "append"])("Hugging Face %s chooser", (mode) => {
  const chooser = () =>
    mode === "create" ? (
      <AddDatasetDrawer open onClose={h.close} refreshGrid={h.refresh} />
    ) : (
      <AddRowDrawer open onClose={h.close} />
    );

  it.each(["missing entry", "failed read"])(
    "retries a %s without claiming the package is missing",
    async (state) => {
      h.available = undefined;
      mount(chooser(), "/dashboard/develop/d1", state === "missing entry");
      const retry = await screen.findByRole("button", {
        name: "Retry Hugging Face availability",
      });
      const door = screen.getByRole("button", { name: /^Hugging Face/ });
      expect(door).toHaveAttribute("aria-disabled", "true");
      expect(
        within(door).getAllByText(
          "Could not check Hugging Face import availability.",
        )[0],
      ).toBeVisible();
      expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
      expect(
        screen.queryByText(
          "Hugging Face import needs the datasets package on this install.",
        ),
      ).not.toBeInTheDocument();
      fireEvent.click(door);
      fireEvent.keyDown(door, { key: "Enter" });
      fireEvent.keyDown(door, { key: " " });
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/develop/d1",
      );
      expect(h.close).not.toHaveBeenCalled();
      expect(h.post).not.toHaveBeenCalled();
      if (state === "missing entry") expect(h.get).not.toHaveBeenCalled();

      const reads = h.get.mock.calls.length;
      h.get.mockResolvedValue({
        data: { features: { huggingface_import: { allowed: true } } },
      });
      fireEvent.click(retry);
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: /^Hugging Face/ }),
        ).not.toHaveAttribute("aria-disabled", "true"),
      );
      expect(h.get).toHaveBeenCalledTimes(reads + 1);
      for (const args of h.get.mock.calls) {
        expect(args).toEqual([apiPath("/api/capabilities/")]);
      }
      expect(
        screen.queryByRole("button", {
          name: "Retry Hugging Face availability",
        }),
      ).not.toBeInTheDocument();
      expect(h.post).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: /^Hugging Face/ }));
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/huggingface",
      );
    },
  );

  it("keeps a pending read neutral without starting an import", () => {
    h.get.mockImplementation(() => new Promise(() => {}));
    mount(chooser(), "/dashboard/develop/d1", false);
    const door = screen.getByRole("button", { name: /^Hugging Face/ });
    expect(door).toHaveAttribute("aria-disabled", "true");
    expect(
      within(door).getAllByText(
        "Checking Hugging Face import availability.",
      )[0],
    ).toBeVisible();
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry Hugging Face availability" }),
    ).not.toBeInTheDocument();
    expect(h.post).not.toHaveBeenCalled();
  });
});

function expectDisabledHfDoor() {
  const door = screen.getByRole("button", { name: /^Hugging Face/ });
  expect(door).toBeVisible();
  expect(door).toHaveAttribute("aria-disabled", "true");
  expect(within(door).getByText(NOTICE)).toBeVisible();
  fireEvent.click(door);
  fireEvent.keyDown(door, { key: "Enter" });
  fireEvent.keyDown(door, { key: " " });
  expect(screen.getByTestId("location")).toHaveTextContent(
    "/dashboard/develop/d1",
  );
  expect(h.close).not.toHaveBeenCalled();
  expect(h.post).not.toHaveBeenCalled();
}

describe("Import availability", () => {
  it("shows a disabled Hugging Face chooser with the package explanation", () => {
    mount(<AddDatasetDrawer open onClose={h.close} refreshGrid={h.refresh} />);
    expectDisabledHfDoor();
  });

  it("shows a disabled HF Add rows tile and truthful sheet copy", () => {
    mount(<AddRowDrawer open onClose={h.close} />);
    expectDisabledHfDoor();
    expect(
      screen.getByText(
        "Review import availability below; disabled options cannot add rows.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByText("Rows are added to this dataset straight away."),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Hugging Face import needs the datasets package on this install.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByText("Rows of a public dataset, added to this dataset now"),
    ).not.toBeInTheDocument();
  });

  it("restores working copy-in while paste stays absent", () => {
    mount(<AddRowDrawer open onClose={h.close} />);
    const copy = screen.getByRole("button", {
      name: /^Copy from another dataset/,
    });
    expect(copy).toBeVisible();
    expect(copy).not.toHaveAttribute("aria-disabled", "true");
    expect(
      screen.queryByRole("button", { name: /^Paste/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(copy);
    expect(h.copyDialog).toHaveBeenLastCalledWith(
      expect.objectContaining({ open: true, datasetId: "d1" }),
    );
    expect(h.post).not.toHaveBeenCalled();
  });

  it.each([
    [/^Upload a file/, "uploadDialog"],
    [/^Add a blank row/, "emptyDialog"],
  ])(
    "opens the restored classic %s form without staging a row",
    (name, dialog) => {
      mount(<AddRowDrawer open onClose={h.close} />);
      const door = screen.getByRole("button", { name });
      expect(door).toBeVisible();
      expect(door).not.toHaveAttribute("aria-disabled", "true");
      expect(
        within(door).queryByText(/turned off in this release/),
      ).not.toBeInTheDocument();
      fireEvent.click(door);
      expect(h[dialog]).toHaveBeenLastCalledWith(
        expect.objectContaining({ open: true, datasetId: "d1" }),
      );
      expect(h.copyDialog).toHaveBeenLastCalledWith(
        expect.objectContaining({ open: false }),
      );
      expect(h.addOp).not.toHaveBeenCalled();
      expect(h.close).not.toHaveBeenCalled();
      expect(h.post).not.toHaveBeenCalled();
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/develop/d1",
      );
    },
  );

  it("keeps the prototype Add rows subtitle unchanged", () => {
    setLaunchOverrideForTest({ showUnreleased: false, launch: false });
    h.available = true;
    mount(<AddRowDrawer open onClose={h.close} />);
    expect(
      screen.getByText(
        "New rows land as unsaved changes; Save version keeps them.",
      ),
    ).toBeVisible();
    for (const name of [
      /^Upload a file/,
      /^Copy from another dataset/,
      /^Add a blank row/,
      /^Hugging Face/,
      /^Paste/,
    ]) {
      const door = screen.getByRole("button", { name });
      expect(door).toBeVisible();
      expect(door).not.toHaveAttribute("aria-disabled", "true");
    }
    expect(h.post).not.toHaveBeenCalled();
  });

  it.each([true, false])(
    "opens the catalog from the create chooser when available (launch=%s)",
    (launch) => {
      setLaunchOverrideForTest({ launch, madeReal: [] });
      h.available = true;
      mount(
        <AddDatasetDrawer open onClose={h.close} refreshGrid={h.refresh} />,
      );
      const door = screen.getByRole("button", { name: /^Hugging Face/ });
      expect(door).not.toHaveAttribute("aria-disabled", "true");
      fireEvent.click(door);
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/huggingface",
      );
    },
  );

  it.each([true, false])(
    "opens the catalog from Add rows when available (launch=%s)",
    (launch) => {
      setLaunchOverrideForTest({ launch, madeReal: [] });
      h.available = true;
      mount(<AddRowDrawer open onClose={h.close} />);
      const door = screen.getByRole("button", { name: /^Hugging Face/ });
      expect(door).not.toHaveAttribute("aria-disabled", "true");
      fireEvent.click(door);
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/huggingface",
      );
    },
  );

  it("cannot bypass the HF disable through startWith", () => {
    mount(
      <AddDatasetDrawer
        open
        onClose={h.close}
        startWith={{ path: "importFromHuggingface" }}
      />,
    );
    expectDisabledHfDoor();
  });

  it.each([
    undefined,
    { datasetId: "d1", additionalData: "hugging-face-drawer" },
  ])(
    "keeps the HF route visible without redirect or import requests (target %j)",
    (state) => {
      mount(<GuardedHuggingFace />, {
        pathname: "/dashboard/huggingface",
        state,
      });
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/huggingface",
      );
      expect(
        screen.getByRole("heading", { name: "Hugging Face" }),
      ).toBeVisible();
      expect(screen.getByText(NOTICE)).toBeVisible();
      for (const name of ["Create dataset", "Add rows"]) {
        const action = screen.getByRole("button", { name });
        expect(action).toBeVisible();
        expect(action).toBeDisabled();
        fireEvent.click(action);
      }
      expect(h.post).not.toHaveBeenCalled();
      expect(screen.queryByText("Datasets fallback")).not.toBeInTheDocument();
    },
  );

  it("opens the real blank-grid form and submits named/typed columns with zero rows", async () => {
    h.post.mockResolvedValue({ data: { result: { dataset_id: "d1" } } });
    mount(<AddDatasetDrawer open onClose={h.close} refreshGrid={h.refresh} />);
    const door = screen.getByRole("button", {
      name: /^Start with a blank grid/,
    });
    expect(door).toBeVisible();
    expect(door).not.toHaveAttribute("aria-disabled", "true");
    fireEvent.click(door);
    const form = screen.getByRole("dialog", {
      name: "Start with a blank grid",
    });
    fireEvent.change(within(form).getByRole("textbox", { name: /Name/ }), {
      target: { value: "Named grid" },
    });
    fireEvent.change(
      within(form).getByRole("textbox", { name: "Column 1 name" }),
      { target: { value: "question" } },
    );
    fireEvent.mouseDown(
      within(form).getByRole("combobox", { name: "Column 1 type" }),
    );
    fireEvent.click(screen.getByRole("option", { name: "Integer" }));
    fireEvent.click(
      within(form).getByRole("button", { name: "Create dataset" }),
    );
    await waitFor(() =>
      expect(h.post).toHaveBeenCalledWith(
        "/manual-create",
        {
          dataset_name: "Named grid",
          model_type: "GenerativeLLM",
          number_of_rows: 0,
          number_of_columns: 2,
          columns: [
            { name: "question", type: "integer", role: "input" },
            { name: "expected_output", type: "text", role: "expected" },
          ],
        },
        { headers: { "Content-Type": "application/json" } },
      ),
    );
    await waitFor(() => expect(h.refresh).toHaveBeenCalledOnce());
    // No startWith: the form's onClose returns to the chooser, not its parent.
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Start with a blank grid" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("heading", { name: "New dataset" })).toBeVisible();
    expect(
      screen.getByRole("button", { name: /^Start with a blank grid/ }),
    ).toBeVisible();
    expect(h.close).not.toHaveBeenCalled();
    expect(h.post).toHaveBeenCalledOnce();
  });

  it("opens the real manual-create form through startWith", () => {
    mount(
      <AddDatasetDrawer
        open
        onClose={h.close}
        startWith={{ path: "manuallyCreateDataset", name: "Direct grid" }}
      />,
    );
    const form = screen.getByRole("dialog", {
      name: "Start with a blank grid",
    });
    expect(within(form).getByRole("textbox", { name: /Name/ })).toHaveValue(
      "Direct grid",
    );
    expect(
      within(form).getByRole("combobox", { name: "Column 1 type" }),
    ).toBeVisible();
  });

  it("does not admit the NEW import features (F50 splits ship all-on)", () => {
    for (const id of [
      "dataset.fromProduction",
      "hf.revision",
      "hf.curated",
      "synthetic.extras",
      "dataset.imports.generateRowsTile",
      "dataset.imports.sources",
      "dataset.imports.columnRoles",
      "dataset.imports.transforms",
    ])
      expect(isSurfaceShown(id), id).toBe(false);
    expect(isSurfaceShown("dataset.splits")).toBe(true);
  });
});
