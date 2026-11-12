// @vitest-environment jsdom
// Keep the real capability, ViewMenu, column layout, saved-views hook and
// query error handler. Only unrelated panels/data and HTTP are replaced.
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
import { handleError } from "src/utils/queryErrorHandler";
import {
  launchState,
  setLaunchOverrideForTest,
  surfaceShownIn,
} from "src/launch/launchSwitch";
import { useCommandStore } from "src/sections/common/CommandPalette/commandRegistry";
import { ViewMenu } from "src/sections/common/ux/oneview/ViewMenu";
import { SAVED_VIEWS_NOT_STORED, savedViewsServed } from "src/api/develop/r3";
import { useLayoutStore } from "src/sections/common/ux/oneview/layoutPrefs";
import { SaveViewDialog } from "src/sections/common/ux/SavedViewsMenu";
import DevelopView from "../DevelopView";
import { DATASET_LIST_SURFACE, datasetListCatalog } from "../datasetList";
import { datasetCaps, setRealBackendForTests } from "../realBackendCaps";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  write: vi.fn(),
  toast: vi.fn(),
  noop: () => {},
  Null: () => null,
  data: undefined,
  loading: true,
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: h.get,
      post: h.write,
      put: h.write,
      patch: h.write,
      delete: h.write,
      request: h.write,
    },
  };
});
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: h.toast,
}));
vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Owner", user: { id: "owner" } }),
}));
vi.mock("src/contexts/OrganizationContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useOrganization: () => ({ currentOrganizationId: "fresh-org" }),
}));
vi.mock("src/contexts/WorkspaceContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useWorkspace: () => ({ currentWorkspaceId: "fresh-workspace" }),
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: h.noop,
  handleOnDocsClicked: h.noop,
}));
vi.mock("../hooks/useDatasetListQuery", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetListQuery: () => ({
    data: h.data,
    isLoading: h.loading,
    isError: false,
    refetch: h.noop,
  }),
}));
vi.mock("../organize/realFolderApi", async (importOriginal) => ({
  ...(await importOriginal()),
  useRealFolderInventory: () => ({
    data: { folders: [], all_count: h.data?.total ?? 0, starred_count: 0 },
    isError: false,
    refetch: h.noop,
  }),
}));
vi.mock("src/sections/common/ux/useShortcuts", async (importOriginal) => ({
  ...(await importOriginal()),
  default: h.noop,
  openShortcutSheet: h.noop,
}));

// Replace unrelated UI; keep ViewMenu and useSavedViews real.
vi.mock("src/components/iconify", () => ({ default: h.Null }));
vi.mock("src/components/svg-color", () => ({ default: h.Null }));
vi.mock("src/components/data-table", async (importOriginal) => ({
  ...(await importOriginal()),
  DataTable: h.Null,
  DataTablePagination: h.Null,
}));
vi.mock("src/sections/develop-detail/Common/ConfigureDatasetModal", () => ({
  default: h.Null,
}));
vi.mock("src/sections/develop-detail/BaseColumnsDrawer", () => ({
  default: h.Null,
}));
vi.mock("src/sections/common/export/ExportDialog", async (importOriginal) => ({
  ...(await importOriginal()),
  DatasetExportDialog: h.Null,
}));
vi.mock("../DuplicateDataset/DuplicateDataset", () => ({ default: h.Null }));
vi.mock("../organize/DatasetSettingsDialog", () => ({ default: h.Null }));
vi.mock("../hooks/useDatasetStars", async (importOriginal) => ({
  ...(await importOriginal()),
  default: h.noop,
}));
vi.mock("../organize/useDatasetOrganize", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => ({ openFor: h.noop }),
}));
vi.mock("../organize/useFolderActions", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => ({ open: h.noop }),
}));
vi.mock("../organize/FolderRail", () => ({ default: h.Null }));
vi.mock("../organize/DatasetListControls", async (importOriginal) => ({
  ...(await importOriginal()),
  DatasetFilterChips: h.Null,
  DatasetListControls: h.Null,
}));

const VALIDATION_MESSAGE =
  'surface: "datasetList" is not a valid choice.; scope: Must be a valid UUID.';
const DATASET_ID = "11111111-1111-4111-8111-111111111111";
const EMPTY_VIEWS = {
  data: { status: true, result: { results: [], next: null } },
};
const EMPTY_LIST = { items: [], total: 0, folders: [] };
// The Datasets list reads the app's shared capability answer (dataset star
// support, useDatasetStarSupport). That read is not a saved-views request and
// never toasts (meta.errorHandled); every other GET is still unexpected.
const CAPABILITIES_URL = "/api/capabilities/";
const CAPABILITIES = {
  data: { features: { dataset_stars: { allowed: true } } },
};
const otherGets = () =>
  h.get.mock.calls.filter(([url]) => url !== CAPABILITIES_URL);
let client;

const withClient = (page) => (
  <QueryClientProvider client={client}>{page}</QueryClientProvider>
);
const mount = (page = <DevelopView />) =>
  renderWithRouter(withClient(page), { route: "/dashboard/develop" });
const listViewCommands = () =>
  useCommandStore.getState().sources[`view:${DATASET_LIST_SURFACE}:`] ?? [];

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  useLayoutStore.setState({ byKey: {} });
  h.toast.mockReset();
  h.data = undefined;
  h.loading = true;
  // Visibility is on, as in the review build. It must not imply support for
  // workspace-scoped saved views on a server with dataset-only persistence.
  setLaunchOverrideForTest({
    launch: true,
    madeReal: ["list.organize.query"],
  });
  setRealBackendForTests(true);
  h.get.mockReset().mockImplementation((url, { params } = {}) => {
    if (url === CAPABILITIES_URL) return Promise.resolve(CAPABILITIES);
    if (url !== endpoints.develop.r3.savedViews)
      throw new Error(`Unexpected GET: ${url}`);
    if (params.surface === DATASET_LIST_SURFACE)
      return Promise.reject({ statusCode: 400, result: VALIDATION_MESSAGE });
    if (params.surface === "grid" && params.scope === DATASET_ID)
      return Promise.resolve(EMPTY_VIEWS);
    throw new Error("Unexpected saved-view scope");
  });
  h.write.mockReset().mockImplementation(() => {
    throw new Error("Unexpected write");
  });
  client = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
});

afterEach(() => {
  cleanup();
  client.clear();
  setLaunchOverrideForTest(null);
  setRealBackendForTests(null);
  useLayoutStore.setState({ byKey: {} });
});

describe("WALK-DATASETS-LANDING-TOAST", () => {
  it("keeps list saved views shown with the list; the hook decides what the server is asked", () => {
    // Never hide a visible control: the Saved views part of the list follows
    // the list's own visibility (its registry owner, list.organize.query).
    // Whether a request is made is useSavedViews' job (savedViewsServed).
    expect(surfaceShownIn("list.organize.query", launchState())).toBe(true);
    expect(datasetCaps()).toMatchObject({
      real: true,
      listFilters: true,
      listSavedViews: true,
    });
    expect(datasetCaps(false).listSavedViews).toBe(true);
    expect(savedViewsServed(DATASET_LIST_SURFACE, undefined, true)).toBe(false);
  });

  it("says saving views here is not available in this release", () => {
    expect(SAVED_VIEWS_NOT_STORED).toContain("Not available in this release");
    // Plain words, never the server's validation sentence.
    expect(SAVED_VIEWS_NOT_STORED).not.toMatch(/valid choice|valid UUID/);
  });

  it("never asks for list saved views, even when a page passes them", async () => {
    // The hook itself refuses surfaces this server keeps no views for, so a
    // page (or the shell) that passes one sends no request and no toast.
    mount(
      <ViewMenu
        surface={DATASET_LIST_SURFACE}
        sections={false}
        savedViews={{ state: {}, onApply: h.noop }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "View options" }));
    const menu = within(screen.getByRole("dialog", { name: "View options" }));
    expect(menu.getByText(SAVED_VIEWS_NOT_STORED)).toBeVisible();
    expect(menu.getByRole("button", { name: "Save as…" })).toBeDisabled();
    expect(otherGets()).toEqual([]);
    expect(h.toast).not.toHaveBeenCalled();
  });

  it("asks only for the dataset grid on this server", () => {
    expect(savedViewsServed("grid", DATASET_ID, true)).toBe(true);
    expect(savedViewsServed("grid", "", true)).toBe(false);
    expect(savedViewsServed("grid", "not-a-uuid", true)).toBe(false);
    for (const surface of [
      DATASET_LIST_SURFACE,
      "leaderboard",
      "results",
      "rowLayout",
    ])
      expect(savedViewsServed(surface, DATASET_ID, true)).toBe(false);
    expect(savedViewsServed(null, DATASET_ID, true)).toBe(false);
    // The prototype world keeps every surface.
    expect(savedViewsServed("leaderboard", "", false)).toBe(true);
    expect(savedViewsServed("grid", "", false)).toBe(false);
  });

  it("a page whose views this server does not keep says so, with no request", async () => {
    mount(
      <ViewMenu
        surface="leaderboard"
        scopeId={DATASET_ID}
        sections={false}
        savedViews={{ state: {}, onApply: h.noop }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "View options" }));
    const menu = within(screen.getByRole("dialog", { name: "View options" }));
    expect(menu.getByText(SAVED_VIEWS_NOT_STORED)).toBeVisible();
    expect(menu.getByRole("button", { name: "Save as…" })).toBeDisabled();
    expect(otherGets()).toEqual([]);
    expect(h.write).not.toHaveBeenCalled();
    expect(h.toast).not.toHaveBeenCalled();
  });

  it("a refused grid read shows in the menu, never as a toast", async () => {
    h.get.mockReset().mockImplementation(() =>
      Promise.reject({ statusCode: 400, result: VALIDATION_MESSAGE }),
    );
    mount(
      <ViewMenu
        surface="grid"
        scopeId={DATASET_ID}
        sections={false}
        savedViews={{ state: {}, onApply: h.noop }}
      />,
    );
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(client.isFetching()).toBe(0));
    fireEvent.click(screen.getByRole("button", { name: "View options" }));
    const menu = within(screen.getByRole("dialog", { name: "View options" }));
    expect(menu.getByText("Couldn\u2019t load saved views.")).toBeVisible();
    expect(h.toast).not.toHaveBeenCalled();
  });

  it("lands in a fresh org without a saved-views request or toast while loading or empty", async () => {
    // The first loading render includes ViewMenu even though the completed
    // empty list switches to the first-run screen. Test both, not just empty.
    let page;
    await act(async () => {
      page = mount();
    });
    expect(screen.getByRole("button", { name: "View options" })).toBeVisible();
    expect(otherGets()).toEqual([]);
    expect(h.toast).not.toHaveBeenCalled();

    await act(async () => {
      h.data = EMPTY_LIST;
      h.loading = false;
      page.rerender(withClient(<DevelopView />));
    });
    expect(screen.getByText("Add your first dataset")).toBeVisible();
    expect(otherGets()).toEqual([]);
    expect(h.write).not.toHaveBeenCalled();
    expect(h.toast).not.toHaveBeenCalled();
  });

  it("lands quietly when the capability read fails: no saved-views request, no toast", async () => {
    h.get.mockImplementation((url) => {
      if (url === CAPABILITIES_URL)
        return Promise.reject({ statusCode: 500, result: "Server error" });
      throw new Error(`Unexpected GET: ${url}`);
    });
    h.data = EMPTY_LIST;
    h.loading = false;
    mount();
    expect(screen.getByText("Add your first dataset")).toBeVisible();
    await waitFor(() => expect(client.isFetching()).toBe(0), {
      timeout: 5000,
    });
    expect(otherGets()).toEqual([]);
    expect(h.write).not.toHaveBeenCalled();
    expect(h.toast).not.toHaveBeenCalled();
  });

  it("keeps Saved views on the Datasets list, says it is not available, and asks the server nothing", async () => {
    h.loading = false;
    h.data = {
      ...EMPTY_LIST,
      items: [{ id: DATASET_ID, name: "Sample dataset" }],
      total: 1,
    };
    mount();
    const columnCount = datasetListCatalog([], true).length;
    expect(listViewCommands().map(({ id }) => id)).toEqual([
      `view.options.${DATASET_LIST_SURFACE}`,
      `view.reset.${DATASET_LIST_SURFACE}`,
      `view.save.${DATASET_LIST_SURFACE}`,
    ]);
    fireEvent.click(screen.getByRole("button", { name: "View options" }));
    const menu = within(screen.getByRole("dialog", { name: "View options" }));
    expect(menu.getByText(`3 of ${columnCount} columns shown`)).toBeVisible();
    // The section stays, with the reason; Save as… is shown disabled and the
    // link (which needs no server) still works.
    expect(menu.getByText("Saved views")).toBeVisible();
    expect(menu.getByText(SAVED_VIEWS_NOT_STORED)).toBeVisible();
    expect(menu.getByRole("button", { name: "Save as…" })).toBeDisabled();
    expect(
      menu.getByRole("button", { name: "Copy link to this view" }),
    ).toBeEnabled();
    expect(menu.queryByText(VALIDATION_MESSAGE)).toBeNull();
    expect(menu.getByRole("button", { name: "Reset view" })).toBeDisabled();

    fireEvent.click(menu.getByRole("button", { name: "Edit…" }));
    const experiments = await screen.findByRole("checkbox", {
      name: "Show Experiments",
    });
    expect(experiments).not.toBeChecked();
    fireEvent.click(experiments);
    expect(experiments).toBeChecked();
    fireEvent.keyDown(screen.getByRole("listbox", { name: "Columns" }), {
      key: "Escape",
    });
    await waitFor(() =>
      expect(screen.queryByRole("listbox", { name: "Columns" })).toBeNull(),
    );
    fireEvent.click(screen.getByRole("button", { name: "View options" }));
    const changedMenu = within(
      screen.getByRole("dialog", { name: "View options" }),
    );
    expect(
      changedMenu.getByText(`4 of ${columnCount} columns shown`),
    ).toBeVisible();
    const reset = changedMenu.getByRole("button", { name: "Reset view" });
    expect(reset).toBeEnabled();
    fireEvent.click(reset);
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "View options" })).toBeNull(),
    );
    fireEvent.click(screen.getByRole("button", { name: "View options" }));
    expect(screen.getByText(`3 of ${columnCount} columns shown`)).toBeVisible();
    expect(screen.getByRole("button", { name: "Reset view" })).toBeDisabled();
    expect(otherGets()).toEqual([]);
    expect(h.write).not.toHaveBeenCalled();
    expect(h.toast).not.toHaveBeenCalled();
  });

  it("Save view as… on the Datasets list opens the View menu, not a dialog that fails", async () => {
    h.loading = false;
    h.data = {
      ...EMPTY_LIST,
      items: [{ id: DATASET_ID, name: "Sample dataset" }],
      total: 1,
    };
    mount();
    const save = listViewCommands().find(
      ({ id }) => id === `view.save.${DATASET_LIST_SURFACE}`,
    );
    expect(save).toBeDefined();
    expect(save.label).toBe("Save view as…");
    act(() => {
      save.run();
    });
    const menu = within(
      await screen.findByRole("dialog", { name: "View options" }),
    );
    expect(menu.getByText(SAVED_VIEWS_NOT_STORED)).toBeVisible();
    expect(menu.getByRole("button", { name: "Save as…" })).toBeDisabled();
    expect(screen.queryByRole("dialog", { name: "Save this view" })).toBeNull();
    expect(otherGets()).toEqual([]);
    expect(h.write).not.toHaveBeenCalled();
    expect(h.toast).not.toHaveBeenCalled();
  });

  it("a save this server cannot keep says why, not 'Try again'", async () => {
    const notStored = new Error(SAVED_VIEWS_NOT_STORED);
    const onSave = vi.fn().mockRejectedValue(notStored);
    mount(<SaveViewDialog open onClose={h.noop} onSave={onSave} />);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "My list view" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save view" }));
    expect(await screen.findByText(SAVED_VIEWS_NOT_STORED)).toBeVisible();
    expect(onSave).toHaveBeenCalledWith("My list view", { shared: false });
    expect(screen.queryByText("Couldn't save the view. Try again.")).toBeNull();
    expect(otherGets()).toEqual([]);
    expect(h.write).not.toHaveBeenCalled();
    expect(h.toast).not.toHaveBeenCalled();
  });

  it("still loads supported grid saved views with a dataset UUID", async () => {
    mount(
      <ViewMenu
        surface="grid"
        scopeId={DATASET_ID}
        sections={false}
        savedViews={{ state: {}, onApply: h.noop }}
      />,
    );
    await waitFor(() => expect(client.isFetching()).toBe(0));
    expect(h.get).toHaveBeenCalledTimes(1);
    expect(h.get).toHaveBeenCalledWith(
      endpoints.develop.r3.savedViews,
      { params: { surface: "grid", scope: DATASET_ID } },
    );
    expect(h.toast).not.toHaveBeenCalled();
  });
});
