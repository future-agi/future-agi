import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter } from "react-router-dom";
import { workspacesListKey } from "src/api/workspaces/list";
import { getWorkspaceQueryOptions } from "../../UserManagementV2/getWorkspaceQueryOptions";
import WorkspaceGeneral from "../WorkspaceGeneral";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  enqueueSnackbar: vi.fn(),
  updateWorkspaceName: vi.fn(),
  currentWorkspaceId: "ws-1",
  workspace: { id: "ws-1", display_name: "Before", user_ws_level: 9 },
}));

vi.mock("src/api/workspaces/list", () => ({
  workspacesListKey: ["workspaces-list"],
  useWorkspaceFromList: () => ({
    workspace: mocks.workspace,
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ workspaceId: "ws-1" }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: mocks.currentWorkspaceId,
    updateWorkspaceName: mocks.updateWorkspaceName,
  }),
}));
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
}));
vi.mock("src/components/loading-screen", () => ({
  LoadingScreen: () => null,
}));
vi.mock("src/sections/common/Access/WorkspaceSharingCard", () => ({
  default: () => null,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, put: mocks.put },
  endpoints: {
    workspace: {
      workspaceList: "/workspaces/",
      workspaceUpdate: (id) => `/workspaces/${id}/`,
    },
  },
}));

const directoryOptions = [
  getWorkspaceQueryOptions({ pageNumber: 0, sort: [], search: "" }),
  getWorkspaceQueryOptions({
    pageNumber: 1,
    sort: [{ columnId: "display_name", type: "ascending" }],
    search: "e",
  }),
];
const settingsKey = ["user-workspaces-for-settings"];
const switcherKey = [...workspacesListKey, "org-1"];
const unrelatedKey = ["unrelated-directory"];
let client;

beforeEach(() => {
  vi.clearAllMocks();
  mocks.currentWorkspaceId = "ws-1";
  // Model persisted data only at the HTTP boundary. The component's mutation,
  // success callback, directory query options and QueryClient are all real.
  let savedName = "Before";
  mocks.get.mockReset().mockImplementation(async () => ({
    data: {
      count: 1,
      results: [{ id: "ws-1", display_name: savedName }],
    },
  }));
  mocks.put.mockReset().mockImplementation(async (_url, payload) => {
    savedName = payload.display_name;
    return { data: { workspace: { id: "ws-1", display_name: savedName } } };
  });
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 60_000 },
      mutations: { retry: false },
    },
  });
  client.setQueryData(settingsKey, []);
  client.setQueryData(switcherKey, { pages: [], pageParams: [] });
  client.setQueryData(unrelatedKey, []);
});

afterEach(() => {
  cleanup();
  client.clear();
});

const saveRename = () => {
  const view = render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <WorkspaceGeneral />
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>,
  );
  expect(screen.getByRole("textbox", { name: "Workspace name" })).toHaveValue(
    "Before",
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Workspace name" }), {
    target: { value: "After" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  return view;
};

describe("H15-C25-003 workspace rename directory cache", () => {
  it.each(["ws-1", "ws-other"])(
    "refetches the saved name on return without reload (current workspace: %s)",
    async (currentWorkspaceId) => {
      mocks.currentWorkspaceId = currentWorkspaceId;
      // Enter the directory, caching both the default and a paged/sorted/search
      // variant. These fetchQuery entries have no observers when on General.
      for (const options of directoryOptions) {
        const before = await client.fetchQuery(options);
        expect(before.data.results[0].display_name).toBe("Before");
        expect(options.staleTime).toBe(Infinity);
      }
      const view = saveRename();
      await waitFor(() =>
        expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
          "Workspace updated",
          {
            variant: "success",
          },
        ),
      );
      expect(mocks.put).toHaveBeenCalledWith("/workspaces/ws-1/", {
        display_name: "After",
      });
      if (currentWorkspaceId === "ws-1") {
        expect(mocks.updateWorkspaceName).toHaveBeenCalledWith("After");
      } else {
        expect(mocks.updateWorkspaceName).not.toHaveBeenCalled();
      }
      expect(client.getQueryState(settingsKey).isInvalidated).toBe(true);
      expect(client.getQueryState(switcherKey).isInvalidated).toBe(true);
      expect(client.getQueryState(unrelatedKey).isInvalidated).toBe(false);

      // Leave General and request the same directory keys with the same client,
      // inside the one-minute retention window: no reload, eviction or new key.
      view.unmount();
      for (const options of directoryOptions) {
        const after = await client.fetchQuery(options);
        expect(after.data.results[0].display_name).toBe("After");
      }
      expect(mocks.get).toHaveBeenCalledTimes(directoryOptions.length * 2);
    },
  );

  it("keeps directory caches and current workspace unchanged when saving fails", async () => {
    await client.fetchQuery(directoryOptions[0]);
    mocks.put.mockRejectedValueOnce(new Error("Save failed"));
    const view = saveRename();
    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Failed to update workspace",
        { variant: "error" },
      ),
    );
    expect(mocks.updateWorkspaceName).not.toHaveBeenCalled();
    for (const key of [
      directoryOptions[0].queryKey,
      settingsKey,
      switcherKey,
    ]) {
      expect(client.getQueryState(key).isInvalidated).toBe(false);
    }
    view.unmount();
    const unchanged = await client.fetchQuery(directoryOptions[0]);
    expect(unchanged.data.results[0].display_name).toBe("Before");
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });
});
