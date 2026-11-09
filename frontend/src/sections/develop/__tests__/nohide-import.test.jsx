// DSX J09 feature-age ruling, release-lead 767/769/770: pre-existing doors
// stay visible. GE-2 disables HF; the named/typed manual grid remains usable.
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
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { launchRedirect } from "src/launch/launchRoutes";
import AddDatasetDrawer from "../AddDatasetDrawer/AddDatasetDrawer";
import AddRowDrawer from "../AddRowDrawer/AddRowDrawer";
import HuggingFacePage from "src/pages/dashboard/huggingface/HuggingFace";

const h = vi.hoisted(() => ({
  post: vi.fn(),
  close: vi.fn(),
  refresh: vi.fn(),
  copyDialog: vi.fn(),
  uploadDialog: vi.fn(),
  emptyDialog: vi.fn(),
  addOp: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: (...args) => h.post(...args) },
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
  "Hugging Face imports can leave imported cells blank; they are turned off in this release. Instead, create a dataset from an uploaded file or copy rows from another dataset.";
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
 */
function mount(node, entry = "/dashboard/develop/d1") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  clients.push(client);
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
    experimentsBasic: false,
    madeReal: madeRealSurfaceIds(),
  });
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

describe("NOHIDE import under the real launch claims", () => {
  it("shows a disabled HF chooser door and the approved notice", () => {
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
      screen.getByText("Hugging Face imports are unavailable in this release."),
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
    setLaunchOverrideForTest({ launch: false });
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
