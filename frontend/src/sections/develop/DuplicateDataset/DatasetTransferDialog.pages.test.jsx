import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";

// The destination list of the transfer dialog comes from the real
// useWorkspacesList (an infinite query over the paginated workspace list,
// 100 a page); only the HTTP client and the organization are stubbed.
const fixtures = vi.hoisted(() => ({
  LIST: "/accounts/workspace/list/",
  get: vi.fn(),
  post: vi.fn(),
  notify: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: fixtures.get, post: fixtures.post },
  endpoints: {
    develop: {
      cloneDataset: (id) => `/model-hub/develops/clone-dataset/${id}/`,
    },
    workspaces: { list: fixtures.LIST },
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-1", isReady: true }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: "workspace-a",
    switchWorkspace: vi.fn(),
  }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("notistack", () => ({ enqueueSnackbar: fixtures.notify }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

import DuplicateDataset from "./DuplicateDataset";

const { LIST } = fixtures;
const TRANSFER = "/model-hub/datasets/dataset-a/transfer/";
const selected = {
  id: "dataset-a",
  name: "Support",
  datasetType: "GenerativeLLM",
};
const workspace = (id, name, level) => ({ id, name, user_ws_level: level });
const readOnly = (page, count) =>
  Array.from({ length: count }, (_, i) =>
    workspace(`read-${page}-${i}`, `Read only ${page}.${i}`, 1),
  );
// Three pages (100, 100 and 2 workspaces) as the backend sends them: the
// writable workspaces Research and Billing are on the second and the third
// page only.
const PAGES = [
  [
    workspace("workspace-a", "Current workspace", 8),
    workspace("workspace-b", "Other workspace", 3),
    ...readOnly(1, 98),
  ],
  [...readOnly(2, 99), workspace("workspace-r", "Research", 3)],
  [
    workspace("workspace-z", "Billing", 3),
    workspace("workspace-v", "Viewers only", 1),
  ],
];
const COUNT = PAGES.flat().length;
const listPage = (number) => ({
  data: {
    count: COUNT,
    next: number < PAGES.length ? `${LIST}?limit=100&page=${number + 1}` : null,
    previous: number > 1 ? `${LIST}?limit=100&page=${number - 1}` : null,
    results: PAGES[number - 1],
    total_pages: PAGES.length,
    current_page: number,
  },
});
const pagesAsked = () =>
  fixtures.get.mock.calls.map(([url, config]) => [url, config?.params]);
const asked = (...numbers) =>
  numbers.map((page) => [LIST, { page, limit: 100 }]);

let client;
function openDialog({ open = true, cached = 0 } = {}) {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  if (cached) {
    // What another view (the workspace switcher) already loaded into the
    // shared list cache.
    const numbers = PAGES.slice(0, cached).map((_, i) => i + 1);
    client.setQueryData(["workspaces-list", "org-1"], {
      pages: numbers.map(listPage),
      pageParams: numbers,
    });
  }
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <DuplicateDataset
        open={open}
        selected={selected}
        onClose={onClose}
        refreshGrid={vi.fn()}
      />
    </QueryClientProvider>,
  );
  return { onClose };
}

async function openDestinations() {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "To workspace" }));
  return within(await screen.findByRole("listbox"));
}
const names = (menu) =>
  menu.getAllByRole("option").map((option) => option.textContent);

beforeEach(() => {
  vi.clearAllMocks();
  setRealBackendForTests(true);
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: new Set(["dataset.duplicateMove"]),
  });
  fixtures.get.mockImplementation((url, config) =>
    Promise.resolve(listPage(config.params.page)),
  );
});

afterEach(() => {
  cleanup();
  client?.clear();
  setRealBackendForTests(null);
  setLaunchOverrideForTest(null);
});

describe("Duplicate into a workspace beyond the first page of the list", () => {
  it("loads every page while open and offers the writable workspaces on later pages", async () => {
    openDialog();
    await waitFor(() => expect(fixtures.get).toHaveBeenCalledTimes(3));
    expect(pagesAsked()).toEqual(asked(1, 2, 3));
    const menu = await openDestinations();
    await menu.findByRole("option", { name: "Billing" });
    expect(names(menu)).toEqual([
      "Current workspace (this workspace)",
      "Other workspace",
      "Research",
      "Billing",
    ]);
    // The last page ends the loading, and nothing was copied or previewed.
    expect(fixtures.get).toHaveBeenCalledTimes(3);
    expect(fixtures.post).not.toHaveBeenCalled();
  });

  it("continues after the pages another view already loaded", async () => {
    openDialog({ cached: 2 });
    await waitFor(() => expect(fixtures.get).toHaveBeenCalledOnce());
    expect(pagesAsked()).toEqual(asked(3));
    const menu = await openDestinations();
    await menu.findByRole("option", { name: "Billing" });
    expect(names(menu)).toEqual([
      "Current workspace (this workspace)",
      "Other workspace",
      "Research",
      "Billing",
    ]);
    expect(fixtures.get).toHaveBeenCalledOnce();
  });

  it("copies into a workspace that only the last page lists", async () => {
    // Needs the generated api-surface bytes (the transfer route resolves
    // through apiPath), like the other dialog tests that call the route.
    fixtures.post.mockResolvedValue({
      data: {
        result: {
          mode: "copy",
          dry_run: false,
          source_dataset_id: "dataset-a",
          dataset_id: "copy-id",
          dataset_name: "Copy of Support",
          from_workspace: { id: "workspace-a", name: "Current workspace" },
          to_workspace: { id: "workspace-z", name: "Billing" },
          plan: { rows: 2, columns: 1, columns_left_out: 0 },
        },
      },
    });
    const { onClose } = openDialog();
    await waitFor(() => expect(fixtures.get).toHaveBeenCalledTimes(3));
    const menu = await openDestinations();
    fireEvent.click(await menu.findByRole("option", { name: "Billing" }));
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(fixtures.post).toHaveBeenCalledExactlyOnceWith(TRANSFER, {
      to_workspace_id: "workspace-z",
      mode: "copy",
      dry_run: false,
    });
    expect(fixtures.notify).toHaveBeenCalledWith(
      "Copied to Billing",
      expect.objectContaining({ variant: "success" }),
    );
  });

  it("stops at a page that fails and keeps offering the workspaces it has", async () => {
    fixtures.get.mockImplementation((url, config) =>
      config.params.page === 2
        ? Promise.reject(new Error("The list is unavailable."))
        : Promise.resolve(listPage(config.params.page)),
    );
    openDialog();
    await waitFor(() => expect(fixtures.get).toHaveBeenCalledTimes(2));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(pagesAsked()).toEqual(asked(1, 2));
    expect(names(await openDestinations())).toEqual([
      "Current workspace (this workspace)",
      "Other workspace",
    ]);
  });

  it("asks for no workspaces while it is closed", async () => {
    openDialog({ open: false });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(fixtures.get).not.toHaveBeenCalled();
    expect(fixtures.post).not.toHaveBeenCalled();
  });
});
