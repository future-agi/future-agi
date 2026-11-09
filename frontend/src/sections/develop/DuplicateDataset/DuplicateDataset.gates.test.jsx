import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const fixtures = vi.hoisted(() => ({
  prototype: false,
  post: vi.fn(),
  workspaces: vi.fn(),
  notify: vi.fn(),
}));

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => fixtures.prototype,
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

let client;
const selected = {
  id: "dataset-a",
  name: "Source dataset",
  datasetType: "GenerativeLLM",
};

function openDialog(open = true) {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onClose = vi.fn();
  const refreshGrid = vi.fn();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <DuplicateDataset
        open={open}
        selected={selected}
        onClose={onClose}
        refreshGrid={refreshGrid}
      />
    </QueryClientProvider>,
  );
  return { onClose, refreshGrid, invalidate };
}

beforeEach(() => {
  fixtures.prototype = false;
  vi.clearAllMocks();
  fixtures.workspaces.mockReturnValue({
    data: [
      { id: "workspace-a", name: "Current workspace", user_ws_role: "Owner" },
      { id: "workspace-b", name: "Other workspace", user_ws_role: "Owner" },
    ],
  });
  fixtures.post.mockResolvedValue({
    data: { result: { dataset_id: "copy-id", dataset_name: "Named copy" } },
  });
});

afterEach(() => {
  cleanup();
  client?.clear();
});

describe("Duplicate dataset on the classic backend", () => {
  it("does not post a dry run or fetch transfer workspaces when opened", async () => {
    openDialog();
    await act(async () => {});
    expect(fixtures.post).not.toHaveBeenCalled();
    expect(fixtures.workspaces).toHaveBeenLastCalledWith({ enabled: false });
  });

  it("hides every unsupported history, transfer and preview control", () => {
    openDialog();
    expect(screen.getByText("Duplicate “Source dataset”")).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "History" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "To workspace" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Check first" })).toBeNull();
    expect(screen.queryByRole("radio", { name: "Move" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText(/Copy it with its history/)).toBeNull();
  });

  it("sends only the supported name on explicit confirmation and refreshes both lists", async () => {
    const { onClose, refreshGrid, invalidate } = openDialog();
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Named copy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(fixtures.post).toHaveBeenCalledOnce();
    const [url, body] = fixtures.post.mock.calls[0];
    expect(url).toBe("/model-hub/develops/clone-dataset/dataset-a/");
    expect(Object.fromEntries(body.entries())).toEqual({
      new_dataset_name: "Named copy",
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["datasets", "list"] });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["develop", "dataset-name-list"],
    });
    expect(refreshGrid).toHaveBeenCalledOnce();
  });

  it("refuses a whitespace-only name without writing", async () => {
    openDialog();
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() =>
      expect(fixtures.notify).toHaveBeenCalledWith("Please enter a name", {
        variant: "error",
      }),
    );
    expect(fixtures.post).not.toHaveBeenCalled();
  });

  it("keeps a refused copy open and reports the backend error", async () => {
    fixtures.post.mockRejectedValue({
      response: { data: { result: "Dataset not found" } },
    });
    const { onClose, refreshGrid } = openDialog();
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Named copy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await screen.findByText("Dataset not found");
    expect(fixtures.post).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
    expect(refreshGrid).not.toHaveBeenCalled();
  });

  it("does not write while closed or when cancelled", async () => {
    const { onClose } = openDialog();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => {});
    expect(onClose).toHaveBeenCalledOnce();
    expect(fixtures.post).not.toHaveBeenCalled();
  });

  it("leaves a closed dialog inert", async () => {
    openDialog(false);
    await act(async () => {});
    expect(fixtures.post).not.toHaveBeenCalled();
    expect(fixtures.workspaces).toHaveBeenLastCalledWith({ enabled: false });
  });
});

describe("Duplicate dataset preview", () => {
  it("preserves the existing preview controls outside the real-backend build", async () => {
    fixtures.prototype = true;
    openDialog();
    expect(
      screen.getByRole("radiogroup", { name: "History" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "To workspace" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Check first" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(fixtures.post).toHaveBeenCalledOnce());
    expect(fixtures.post.mock.calls[0][1].get("dry_run")).toBe("true");
  });
});
