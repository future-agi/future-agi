import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import DatasetTransferDialog from "./DatasetTransferDialog";

const h = vi.hoisted(() => ({
  post: vi.fn(),
  fetchNextPage: vi.fn(),
  notify: vi.fn(),
  switchWorkspace: vi.fn(),
  workspaces: [
    { id: "workspace-a", name: "Current workspace", user_ws_level: 8 },
    { id: "workspace-b", name: "Other workspace", user_ws_level: 3 },
    { id: "workspace-c", name: "Third workspace", user_ws_level: 3 },
  ],
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, post: h.post },
  };
});
vi.mock("src/api/workspaces/list", async (importOriginal) => ({
  ...(await importOriginal()),
  useWorkspacesList: () => ({
    data: h.workspaces,
    fetchNextPage: h.fetchNextPage,
  }),
}));
vi.mock("src/contexts/WorkspaceContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useWorkspace: () => ({
    currentWorkspaceId: "workspace-a",
    switchWorkspace: h.switchWorkspace,
  }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: h.notify,
}));
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

const TRANSFER = "/model-hub/datasets/dataset-a/transfer/";
const CLONE = "/model-hub/develops/clone-dataset/dataset-a/";
const UNAVAILABLE =
  "Copying to another workspace is not available on this server. You can copy into this workspace instead.";
const selected = { id: "dataset-a", name: "Support" };
// axios rejects with the public error envelope flattened, not AxiosError.
const off = {
  statusCode: 404,
  status: false,
  code: "not_found",
  detail: "Not found.",
  message: "Not found.",
  result: "Not found.",
};
const response = (dryRun = true) => ({
  data: {
    result: {
      dry_run: dryRun,
      mode: "copy",
      dataset_name: "Named copy",
      dataset_id: dryRun ? null : "copy-id",
      from_workspace: { id: "workspace-a", name: "Current workspace" },
      to_workspace: { id: "workspace-b", name: "Other workspace" },
      plan: { rows: 2, columns: 1, columns_left_out: 0 },
    },
  },
});

function deferred() {
  let reject;
  const promise = new Promise((_resolve, fail) => {
    reject = fail;
  });
  return { promise, reject };
}

let client;
function openDialog() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onClose = vi.fn();
  const refreshGrid = vi.fn();
  const content = (open) => (
    <QueryClientProvider client={client}>
      <DatasetTransferDialog
        open={open}
        selected={selected}
        onClose={onClose}
        refreshGrid={refreshGrid}
      />
    </QueryClientProvider>
  );
  const view = render(content(true));
  return {
    onClose,
    refreshGrid,
    reopen: () => view.rerender(content(true)),
    hide: () => view.rerender(content(false)),
  };
}

async function chooseWorkspace(label) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "To workspace" }));
  fireEvent.click(await screen.findByRole("option", { name: label }));
}

beforeEach(() => {
  vi.clearAllMocks();
  h.post.mockReset();
  h.fetchNextPage.mockResolvedValue({ hasNextPage: false });
});
afterEach(() => {
  cleanup();
  client?.clear();
});

describe("W5 duplicate follows an unavailable transfer route", () => {
  it.each(["Check first", "Copy"])(
    "W5-01: %s explains the disabled route and requires an explicit same-workspace choice",
    async (action) => {
      h.post.mockRejectedValueOnce(off);
      const { onClose, refreshGrid } = openDialog();
      await chooseWorkspace("Other workspace");
      fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
        target: { value: "Named copy" },
      });
      fireEvent.click(screen.getByRole("button", { name: action }));
      expect(await screen.findByRole("alert")).toHaveTextContent(UNAVAILABLE);
      expect(screen.queryByText("Not found.")).toBeNull();
      expect(screen.queryByTestId("transfer-plan")).toBeNull();
      expect(screen.queryByRole("button", { name: "Check first" })).toBeNull();
      expect(screen.getByRole("button", { name: "Copy" })).toBeDisabled();
      expect(screen.getByRole("combobox", { name: "To workspace" }))
        .toHaveTextContent("Other workspace");
      expect(h.post).toHaveBeenCalledExactlyOnceWith(TRANSFER, {
        to_workspace_id: "workspace-b",
        mode: "copy",
        dry_run: action === "Check first",
        new_dataset_name: "Named copy",
      });
      expect(onClose).not.toHaveBeenCalled();
      expect(refreshGrid).not.toHaveBeenCalled();
      expect(h.switchWorkspace).not.toHaveBeenCalled();

      // Changing a name must not revive the unsupported transfer action.
      fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
        target: { value: "Renamed copy" },
      });
      expect(screen.getByRole("alert")).toHaveTextContent(UNAVAILABLE);
      expect(screen.getByRole("button", { name: "Copy" })).toBeDisabled();
      // Guard the form handler too, not just the disabled button.
      fireEvent.submit(screen.getByRole("button", { name: "Copy" }).closest("form"));
      await act(async () => {});
      expect(h.post).toHaveBeenCalledOnce();

      fireEvent.click(screen.getByRole("button", { name: "Use this workspace" }));
      expect(h.post).toHaveBeenCalledOnce();
      expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Renamed copy");
      expect(screen.getByRole("button", { name: "Copy" })).toBeEnabled();
      expect(screen.getByRole("combobox", { name: "To workspace" }))
        .toHaveTextContent("Current workspace");
      expect(screen.getByRole("dialog")).toHaveTextContent(
        "Copy rows and input columns into this workspace.",
      );
      // Neither another destination nor an edit can bypass the server refusal.
      fireEvent.mouseDown(screen.getByRole("combobox", { name: "To workspace" }));
      const menu = within(await screen.findByRole("listbox"));
      expect(menu.getByRole("option", { name: "Other workspace" }))
        .toHaveAttribute("aria-disabled", "true");
      expect(menu.getByRole("option", { name: "Third workspace" }))
        .toHaveAttribute("aria-disabled", "true");
      fireEvent.click(menu.getByRole("option", { name: "Current workspace (this workspace)" }));
      h.post.mockResolvedValueOnce({
        data: { result: { dataset_id: "copy-id", dataset_name: "Renamed copy" } },
      });
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
      await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
      expect(h.post).toHaveBeenCalledTimes(2);
      const [url, body] = h.post.mock.calls[1];
      expect(url).toBe(CLONE);
      expect(Object.fromEntries(body.entries())).toEqual({ new_dataset_name: "Renamed copy" });
      expect(refreshGrid).toHaveBeenCalledOnce();
    },
  );

  it("W5-02: a supported server previews and copies with the source route and target in the body", async () => {
    h.post.mockResolvedValueOnce(response()).mockResolvedValueOnce(response(false));
    const { onClose } = openDialog();
    expect(h.post).not.toHaveBeenCalled();
    await chooseWorkspace("Other workspace");
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Named copy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Check first" }));
    expect(await screen.findByTestId("transfer-plan")).toHaveTextContent(
      "Will copy 2 rows · 1 column into Other workspace as “Named copy”.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(h.post.mock.calls).toEqual([true, false].map((dryRun) => [TRANSFER, {
      to_workspace_id: "workspace-b",
      mode: "copy",
      dry_run: dryRun,
      new_dataset_name: "Named copy",
    }]));
    expect(h.switchWorkspace).not.toHaveBeenCalled();
    expect(h.notify).toHaveBeenCalledWith("Copied to Other workspace", expect.any(Object));
  });

  it.each([
    { statusCode: 403, result: "You can't add datasets to that workspace." },
    { statusCode: 409, result: "That name is already taken." },
    { statusCode: 503, result: "Please try again later." },
    { statusCode: 404, result: "Workspace not found." },
    { statusCode: 404, result: "Dataset not found." },
  ])("W5-03: a specific refusal does not turn off transfers ($statusCode, $result)", async (error) => {
    h.post.mockRejectedValueOnce(error);
    openDialog();
    await chooseWorkspace("Other workspace");
    fireEvent.click(screen.getByRole("button", { name: "Check first" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(error.result);
    expect(screen.getByRole("button", { name: "Check first" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Copy" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Use this workspace" })).toBeNull();
    await chooseWorkspace("Third workspace");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("W5-04: a late preview 404 cannot disable the currently selected options", async () => {
    const late = deferred();
    h.post.mockReturnValueOnce(late.promise);
    openDialog();
    await chooseWorkspace("Other workspace");
    fireEvent.click(screen.getByRole("button", { name: "Check first" }));
    await waitFor(() => expect(h.post).toHaveBeenCalledOnce());
    await chooseWorkspace("Third workspace");
    await act(async () => late.reject(off));
    await waitFor(() => expect(screen.getByRole("button", { name: "Check first" })).toBeEnabled());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Copy" })).toBeEnabled();
  });

  it("W5-05: reopening retries server availability rather than persisting a refusal", async () => {
    h.post.mockRejectedValueOnce(off);
    const dialog = openDialog();
    await chooseWorkspace("Other workspace");
    fireEvent.click(screen.getByRole("button", { name: "Check first" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(UNAVAILABLE);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    dialog.hide();
    dialog.reopen();
    h.post.mockResolvedValueOnce(response());
    await chooseWorkspace("Other workspace");
    fireEvent.click(screen.getByRole("button", { name: "Check first" }));
    await screen.findByTestId("transfer-plan");
    expect(h.post).toHaveBeenCalledTimes(2);
  });

  it("W5-06: a late copy 404 after closing does not disable a reopened dialog", async () => {
    const late = deferred();
    h.post.mockReturnValueOnce(late.promise);
    const dialog = openDialog();
    await chooseWorkspace("Other workspace");
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(h.post).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    dialog.hide();
    dialog.reopen();
    await act(async () => late.reject(off));
    await chooseWorkspace("Other workspace");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Check first" })).toBeEnabled();
  });
});
