import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: api,
  endpoints: {
    develop: { getDatasets: () => "/model-hub/develops/get-datasets/" },
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-a" }),
}));
import RealFolderActionDialog from "./RealFolderActionDialog";
import RealMoveToFolderDialog from "./RealMoveToFolderDialog";
import { offeredOrganizeItems } from "./organizeItems";
import { datasetListQueryConfig } from "../hooks/useDatasetListQuery";

let client;
function mount(node) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue({
    data: { result: { folders: [{ id: "folder-a", path: "support" }] } },
  });
  api.post.mockResolvedValue({ data: { result: {} } });
  api.patch.mockResolvedValue({ data: { result: {} } });
  api.delete.mockResolvedValue({ data: { result: {} } });
});
afterEach(() => {
  cleanup();
  client?.clear();
  setLaunchOverrideForTest(null);
});

it("creates a persisted empty folder only on confirmation", async () => {
  const close = vi.fn();
  mount(<RealFolderActionDialog action="create" onClose={close} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Folder path" }), {
    target: { value: "support/billing" },
  });
  expect(api.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save folder" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(api.post).toHaveBeenCalledWith("/model-hub/dataset-folders/", {
    path: "support/billing",
  });
});
it("keeps a refused rename open without pretending to save", async () => {
  api.patch.mockRejectedValue({
    result: { path: ["A folder already exists at the destination."] },
  });
  const close = vi.fn();
  mount(
    <RealFolderActionDialog
      action="rename"
      folder={{ id: "folder-a", path: "support" }}
      onClose={close}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Save folder" }));
  await screen.findByText("A folder already exists at the destination.");
  expect(close).not.toHaveBeenCalled();
  expect(screen.getByDisplayValue("support")).toBeVisible();
});
it("cancels folder deletion without writing", () => {
  const close = vi.fn();
  mount(
    <RealFolderActionDialog
      action="remove"
      folder={{ id: "folder-a", path: "support" }}
      onClose={close}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(api.delete).not.toHaveBeenCalled();
  expect(close).toHaveBeenCalledOnce();
});
it("moves the whole selection atomically by folder identity", async () => {
  const close = vi.fn();
  mount(
    <RealMoveToFolderDialog
      datasets={[{ id: "d1" }, { id: "d2" }]}
      onClose={close}
    />,
  );
  fireEvent.click(await screen.findByText("support"));
  fireEvent.click(screen.getByRole("button", { name: "Move", exact: true }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(api.post).toHaveBeenCalledWith(
    "/model-hub/dataset-folders/move-datasets/",
    { dataset_ids: ["d1", "d2"], folder_id: "folder-a" },
  );
  expect(
    client.getQueryData([
      "datasets",
      "list",
      "folders",
      "org-a",
      "workspace-a",
    ]),
  ).toBeTruthy();
});
it("holds the independently claimed folder action and folder query", () => {
  setLaunchOverrideForTest({
    launch: true,
    madeReal: ["list.organize.folders"],
  });
  expect(offeredOrganizeItems().map((item) => item.kind)).toEqual([]);
  expect(
    datasetListQueryConfig(
      { folderPath: "support", filters: "unsupported" },
      true,
    ).queryKey.at(-1),
  ).not.toHaveProperty("folder_path");
  setLaunchOverrideForTest({ launch: true, madeReal: [] });
  expect(offeredOrganizeItems()).toEqual([]);
  expect(
    datasetListQueryConfig({ folderPath: "support" }, true).queryKey.at(-1),
  ).not.toHaveProperty("folder_path");
});
