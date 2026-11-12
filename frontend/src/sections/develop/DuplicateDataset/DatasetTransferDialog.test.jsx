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
import {
  datasetCaps,
  setRealBackendForTests,
} from "src/sections/develop/realBackendCaps";

const fixtures = vi.hoisted(() => ({
  post: vi.fn(),
  workspaces: vi.fn(),
  fetchNextPage: vi.fn(),
  notify: vi.fn(),
  switchWorkspace: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { post: fixtures.post },
  endpoints: {
    develop: {
      cloneDataset: (id) => `/model-hub/develops/clone-dataset/${id}/`,
    },
  },
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: fixtures.workspaces,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: "workspace-a",
    switchWorkspace: fixtures.switchWorkspace,
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

const TRANSFER = "/model-hub/datasets/dataset-a/transfer/";
const selected = {
  id: "dataset-a",
  name: "Support",
  datasetType: "GenerativeLLM",
};
const claimed = (ids) => ({
  launch: true,
  experimentsBasic: false,
  madeReal: new Set(ids),
});
const result = (overrides = {}) => ({
  data: {
    result: {
      mode: "copy",
      dry_run: true,
      source_dataset_id: "dataset-a",
      dataset_id: null,
      dataset_name: "Copy of Support",
      from_workspace: { id: "workspace-a", name: "Current workspace" },
      to_workspace: { id: "workspace-b", name: "Other workspace" },
      plan: { rows: 2, columns: 1, columns_left_out: 1, leaves_folder: false },
      ...overrides,
    },
  },
});
const WORKSPACE_ROWS = [
  { id: "workspace-a", name: "Current workspace", user_ws_level: 8 },
  {
    id: "workspace-b",
    name: "ws-b",
    display_name: "Other workspace",
    user_ws_level: 3,
  },
  { id: "workspace-c", name: "Read only", user_ws_level: 1 },
  { id: "workspace-d", name: "Not a member", user_ws_level: null },
];
const THIRD = { id: "workspace-e", name: "Third workspace", user_ws_level: 3 };
// What useWorkspacesList returns once its only page is in; a list longer
// than one page is covered by DatasetTransferDialog.pages.test.jsx.
const listed = (rows) => ({
  data: rows,
  fetchNextPage: fixtures.fetchNextPage,
});

/** A request the test settles by hand, after changing the options. */
function deferred() {
  /** @type {(value?: unknown) => void} */
  let resolve;
  /** @type {(reason?: unknown) => void} */
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let client;
function openDialog() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onClose = vi.fn();
  const refreshGrid = vi.fn();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <DuplicateDataset
        open
        selected={selected}
        onClose={onClose}
        refreshGrid={refreshGrid}
      />
    </QueryClientProvider>,
  );
  return { onClose, refreshGrid, invalidate };
}

async function chooseWorkspace(label) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "To workspace" }));
  fireEvent.click(await screen.findByRole("option", { name: label }));
}

beforeEach(() => {
  vi.clearAllMocks();
  setRealBackendForTests(true);
  setLaunchOverrideForTest(claimed(["dataset.duplicateMove"]));
  fixtures.fetchNextPage.mockResolvedValue({ hasNextPage: false });
  fixtures.workspaces.mockReturnValue(listed(WORKSPACE_ROWS));
});

afterEach(() => {
  cleanup();
  client?.clear();
  setRealBackendForTests(null);
  setLaunchOverrideForTest(null);
});

describe("dataset copy and move capability", () => {
  it("is independent of every other list and dataset claim", () => {
    setLaunchOverrideForTest(
      claimed(["dataset.switcher", "list.organize.folders"]),
    );
    expect(datasetCaps(true).duplicateMove).toBe(false);
    setLaunchOverrideForTest(claimed(["dataset.duplicateMove"]));
    const caps = datasetCaps(true);
    expect(caps.duplicateMove).toBe(true);
    expect([
      caps.listFolders,
      caps.listStars,
      caps.settings,
      caps.listFilters,
    ]).toEqual([false, false, false, false]);
  });

  it("keeps the classic dialog when the capability is not claimed", () => {
    setLaunchOverrideForTest(claimed([]));
    openDialog();
    expect(screen.queryByRole("combobox", { name: "To workspace" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Check first" })).toBeNull();
  });
});

describe("Duplicate into another workspace on the real backend", () => {
  it("opens without a request and offers only workspaces you can add datasets to", async () => {
    openDialog();
    await act(async () => {});
    expect(fixtures.post).not.toHaveBeenCalled();
    expect(fixtures.workspaces).toHaveBeenLastCalledWith({ enabled: true });
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "To workspace" }));
    const options = within(await screen.findByRole("listbox")).getAllByRole(
      "option",
    );
    expect(options.map((o) => o.textContent)).toEqual([
      "Current workspace (this workspace)",
      "Other workspace",
    ]);
  });

  it("never offers history, split or experiment options", async () => {
    openDialog();
    await chooseWorkspace("Other workspace");
    expect(screen.queryByRole("radiogroup", { name: "History" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText(/version/i)).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Rows" })).toBeNull();
  });

  it("previews a copy into another workspace with a dry run only", async () => {
    fixtures.post.mockResolvedValue(result());
    openDialog();
    await chooseWorkspace("Other workspace");
    fireEvent.click(screen.getByRole("button", { name: "Check first" }));
    const plan = await screen.findByTestId("transfer-plan");
    expect(fixtures.post).toHaveBeenCalledExactlyOnceWith(TRANSFER, {
      to_workspace_id: "workspace-b",
      mode: "copy",
      dry_run: true,
    });
    expect(plan).toHaveTextContent(
      "Will copy 2 rows · 1 column into Other workspace as “Copy of Support”.",
    );
    expect(plan).toHaveTextContent("1 generated column is not copied.");
  });

  it("copies into another workspace with the chosen name and refreshes both lists", async () => {
    fixtures.post.mockResolvedValue(
      result({
        dry_run: false,
        dataset_id: "copy-id",
        dataset_name: "Named copy",
      }),
    );
    const { onClose, refreshGrid, invalidate } = openDialog();
    await chooseWorkspace("Other workspace");
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "  Named copy " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(fixtures.post).toHaveBeenCalledExactlyOnceWith(TRANSFER, {
      to_workspace_id: "workspace-b",
      mode: "copy",
      new_dataset_name: "Named copy",
      dry_run: false,
    });
    expect(fixtures.notify).toHaveBeenCalledWith(
      "Copied to Other workspace",
      expect.objectContaining({ variant: "success" }),
    );
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["datasets", "list"] });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["develop", "dataset-name-list"],
    });
    expect(refreshGrid).toHaveBeenCalledOnce();
  });

  it("offers a copy only, never a move, even into another workspace", async () => {
    openDialog();
    // The backend refuses every move between workspaces.
    expect(screen.getByText("Duplicate “Support”")).toBeInTheDocument();
    await chooseWorkspace("Other workspace");
    expect(
      screen.queryByRole("radiogroup", { name: "Copy or move" }),
    ).toBeNull();
    expect(screen.queryByRole("radio", { name: "Move" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Move" })).toBeNull();
    expect(screen.queryAllByText(/\bmove/i)).toEqual([]);
    expect(screen.getByRole("textbox", { name: "Name" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Copy" })).toBeEnabled();
    expect(fixtures.post).not.toHaveBeenCalled();
  });

  it("never shows a preview for options that changed while it was loading", async () => {
    fixtures.workspaces.mockReturnValue(listed([...WORKSPACE_ROWS, THIRD]));
    const late = deferred();
    fixtures.post.mockReturnValueOnce(late.promise);
    openDialog();
    await chooseWorkspace("Other workspace");
    const check = screen.getByRole("button", { name: "Check first" });
    fireEvent.click(check);
    await waitFor(() => expect(fixtures.post).toHaveBeenCalledOnce());
    expect(fixtures.post).toHaveBeenLastCalledWith(TRANSFER, {
      to_workspace_id: "workspace-b",
      mode: "copy",
      dry_run: true,
    });
    // Another target while the first preview is still pending; then the
    // first preview answers.
    await chooseWorkspace("Third workspace");
    await act(async () => {
      late.resolve(result());
    });
    await waitFor(() => expect(check).toBeEnabled());
    expect(screen.queryByTestId("transfer-plan")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    // Only a preview of the options on screen is shown, and Copy sends them.
    fixtures.post.mockResolvedValueOnce(
      result({ to_workspace: { id: "workspace-e", name: "Third workspace" } }),
    );
    fireEvent.click(check);
    expect(await screen.findByTestId("transfer-plan")).toHaveTextContent(
      "Will copy 2 rows · 1 column into Third workspace as “Copy of Support”.",
    );
    expect(fixtures.post).toHaveBeenLastCalledWith(TRANSFER, {
      to_workspace_id: "workspace-e",
      mode: "copy",
      dry_run: true,
    });
    fixtures.post.mockResolvedValueOnce(
      result({
        dry_run: false,
        dataset_id: "copy-id",
        to_workspace: { id: "workspace-e", name: "Third workspace" },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() =>
      expect(fixtures.notify).toHaveBeenCalledWith(
        "Copied to Third workspace",
        expect.objectContaining({ variant: "success" }),
      ),
    );
    expect(fixtures.post).toHaveBeenLastCalledWith(TRANSFER, {
      to_workspace_id: "workspace-e",
      mode: "copy",
      dry_run: false,
    });
  });

  it("drops a late preview refusal for options that have since changed", async () => {
    const late = deferred();
    fixtures.post.mockReturnValueOnce(late.promise);
    openDialog();
    await chooseWorkspace("Other workspace");
    const check = screen.getByRole("button", { name: "Check first" });
    fireEvent.click(check);
    await waitFor(() => expect(fixtures.post).toHaveBeenCalledOnce());
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Renamed copy" },
    });
    await act(async () => {
      late.reject({
        response: {
          data: { result: "A dataset with this name already exists." },
        },
      });
    });
    await waitFor(() => expect(check).toBeEnabled());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("transfer-plan")).toBeNull();
  });

  it("keeps a same-workspace copy on the classic duplicate without a preview", async () => {
    fixtures.post.mockResolvedValue({
      data: {
        result: { dataset_id: "copy-id", dataset_name: "Copy of Support" },
      },
    });
    const { onClose } = openDialog();
    expect(screen.queryByRole("button", { name: "Check first" })).toBeNull();
    expect(screen.queryByRole("radio", { name: "Move" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    const [url, body] = fixtures.post.mock.calls[0];
    expect(url).toBe("/model-hub/develops/clone-dataset/dataset-a/");
    expect(Object.fromEntries(body.entries())).toEqual({});
    expect(fixtures.notify).toHaveBeenCalledWith(
      "Duplicated “Support” as “Copy of Support”",
      expect.objectContaining({ variant: "success" }),
    );
  });

  it("keeps the dialog open and reports a refused copy", async () => {
    fixtures.post.mockRejectedValue({
      response: {
        data: { result: "You can't add datasets to that workspace." },
      },
    });
    const { onClose, refreshGrid } = openDialog();
    await chooseWorkspace("Other workspace");
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You can't add datasets to that workspace.",
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(refreshGrid).not.toHaveBeenCalled();
  });
});
