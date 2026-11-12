import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import CreateKnowledgeBaseDrawer from "../CreateKnowledgeBaseDrawer";

const mocks = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  params: {},
  navigate: vi.fn(),
  refetch: vi.fn(),
  snackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: mocks.api,
  endpoints: { knowledge: { knowledgeBase: "/model-hub/knowledge-base/" } },
}));
vi.mock("react-router", () => ({
  useNavigate: () => mocks.navigate,
  useParams: () => mocks.params,
}));
vi.mock("src/api/knowledge-base/files", () => ({
  useKnowledgeBaseList: () => ({ refetch: mocks.refetch }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.snackbar }));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.snackbar }),
}));
vi.mock("src/utils/prototype", () => ({ isPrototypeMode: () => false }));
vi.mock("src/utils/utils", () => ({ getFileExtension: () => "txt" }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: () => null,
}));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));
vi.mock("../SdkInfoDialog", () => ({ default: () => null }));
vi.mock("../UploadKnowledgeBaseSDK", () => ({ default: () => null }));

// Keep the drawer, form state, upload adapter and mutations real. Only replace
// the dropzone and file-row presentation so their status/removal contract is visible.
/* eslint-disable react/prop-types */
vi.mock("src/components/hook-form", () => ({
  RHFUpload: ({ onDrop, disabled }) => (
    <input
      aria-label="Choose files"
      type="file"
      multiple
      disabled={disabled}
      onChange={(event) => onDrop(Array.from(event.target.files), [])}
    />
  ),
}));
vi.mock("../SingleFileDetail", () => ({
  default: ({ file, deleteFile, isLoading }) => (
    <div data-testid={`file-${file.item.name}`} data-status={file.status}>
      <span>{file.item.name}</span>
      <span>{file.statusReason}</span>
      <button
        type="button"
        disabled={isLoading}
        onClick={() => deleteFile(file.status)}
      >
        Remove {file.item.name}
      </button>
    </div>
  ),
}));
/* eslint-enable react/prop-types */

const issue = "Text file is empty or corrupted";
let client;

// The API's 400 body after axios normalization. Validation rejects the entire
// request before persistence, and only the invalid file appears in `files`.
const atomicRejection = (flag = "not_uploaded") => ({
  statusCode: 400,
  result: {
    message: "Some files could not be processed",
    [flag]: true,
    files: [{ name: "blank.txt", error: issue }],
  },
});

beforeEach(() => {
  vi.resetAllMocks();
  mocks.params = {};
  mocks.api.get.mockResolvedValue({ data: { result: { code: "" } } });
  mocks.api.post.mockResolvedValue({ data: { result: { kb_id: "created-kb" } } });
  mocks.api.patch.mockResolvedValue({ data: { result: {} } });
  mocks.api.delete.mockRejectedValue({ statusCode: 400, result: "Missing id" });
});

afterEach(() => {
  cleanup();
  client?.clear();
});

function renderSelection(mode) {
  mocks.params = mode.knowledgeId ? { knowledgeId: mode.knowledgeId } : {};
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  const onClose = vi.fn();
  const refreshGrid = vi.fn();
  const setHasData = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <CreateKnowledgeBaseDrawer
        open
        data={{ name: "Atomic upload" }}
        onClose={onClose}
        refreshGrid={refreshGrid}
        setHasData={setHasData}
      />
    </QueryClientProvider>,
  );
  const good = new File(["Useful knowledge"], "good.txt", { type: "text/plain" });
  const blank = new File(["   \n"], "blank.txt", { type: "text/plain" });
  // Whitespace-only TXT passes the real dropzone's nonzero-byte requirement.
  expect(blank.size).toBeGreaterThan(0);
  fireEvent.change(screen.getByLabelText("Choose files"), {
    target: { files: [good, blank] },
  });
  return { onClose, refreshGrid, setHasData, good, blank };
}

async function rejectUpload(mode, flag = "not_uploaded") {
  const upload = mocks.api[mode.method];
  upload.mockRejectedValueOnce(atomicRejection(flag));
  fireEvent.click(screen.getByRole("button", { name: mode.action }));
  await waitFor(() => {
    expect(upload).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("file-blank.txt")).toHaveAttribute("data-status", "error");
    expect(screen.getByRole("button", { name: "Remove good.txt" })).toBeEnabled();
  });
  expect(screen.getByText(issue)).toBeInTheDocument();
}

describe.each([
  { label: "create", method: "post", action: "Create", knowledgeId: undefined },
  { label: "add files", method: "patch", action: "Add", knowledgeId: "existing-kb" },
])("W5-C-003 atomic upload rejection: $label", (mode) => {
  it("labels local files as selected before any upload", () => {
    renderSelection(mode);

    expect(screen.getByText("Files selected: 2")).toBeInTheDocument();
    expect(screen.queryByText(/Files uploaded:/)).not.toBeInTheDocument();
    expect(screen.getByTestId("file-good.txt")).toHaveAttribute("data-status", "not_started");
    expect(mocks.api[mode.method]).not.toHaveBeenCalled();
  });

  it.each(["not_uploaded", "notUploaded"])(
    "keeps the whole batch local after %s rejection",
    async (flag) => {
      const { onClose, refreshGrid, setHasData } = renderSelection(mode);
      await rejectUpload(mode, flag);

      expect(screen.getByTestId("file-good.txt")).toHaveAttribute("data-status", "not_started");
      expect(screen.getByTestId("file-blank.txt")).toHaveAttribute("data-status", "error");
      expect(document.querySelector('[data-status="success"]')).toBeNull();
      expect(screen.getByText("Files selected: 2")).toBeInTheDocument();
      expect(screen.queryByText(/Files uploaded:/)).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: mode.action })).toBeDisabled();
      expect(onClose).not.toHaveBeenCalled();
      expect(refreshGrid).not.toHaveBeenCalled();
      expect(setHasData).not.toHaveBeenCalled();
      expect(mocks.refetch).not.toHaveBeenCalled();
      expect(mocks.navigate).not.toHaveBeenCalled();
    },
  );

  it("removes a valid but unpersisted file locally after rejection", async () => {
    renderSelection(mode);
    await rejectUpload(mode);

    fireEvent.click(screen.getByRole("button", { name: "Remove good.txt" }));

    expect(mocks.api.delete).not.toHaveBeenCalled();
    expect(screen.queryByTestId("file-good.txt")).not.toBeInTheDocument();
    expect(screen.getByTestId("file-blank.txt")).toHaveAttribute("data-status", "error");
    expect(screen.getByText("Files selected: 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: mode.action })).toBeDisabled();
  });

  it("retries only the remaining local selection after removing the invalid file", async () => {
    const { onClose, refreshGrid, setHasData } = renderSelection(mode);
    await rejectUpload(mode);
    const upload = mocks.api[mode.method];
    expect(upload.mock.calls[0][1].getAll("file").map((file) => file.name).sort()).toEqual([
      "blank.txt",
      "good.txt",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Remove blank.txt" }));

    expect(mocks.api.delete).not.toHaveBeenCalled();
    expect(screen.queryByTestId("file-blank.txt")).not.toBeInTheDocument();
    expect(screen.getByTestId("file-good.txt")).toHaveAttribute("data-status", "not_started");
    expect(screen.getByRole("button", { name: mode.action })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: mode.action }));

    await waitFor(() => {
      expect(upload).toHaveBeenCalledTimes(2);
      expect(onClose).toHaveBeenCalledTimes(1);
    });
    const [url, body, config] = upload.mock.calls[1];
    expect(url).toBe("/model-hub/knowledge-base/");
    expect(body.getAll("file").map((file) => file.name)).toEqual(["good.txt"]);
    expect(body.get("name")).toBe("Atomic upload");
    expect(body.get("kb_id")).toBe(mode.knowledgeId ?? null);
    expect(config.headers["Content-Type"]).toBe("multipart/form-data");
    expect(mocks.api.delete).not.toHaveBeenCalled();
    expect(mocks.refetch).toHaveBeenCalledTimes(1);
    if (mode.knowledgeId) {
      expect(refreshGrid).toHaveBeenCalledTimes(1);
      expect(mocks.navigate).not.toHaveBeenCalled();
    } else {
      expect(setHasData).toHaveBeenCalledWith(true);
      expect(mocks.navigate).toHaveBeenCalledWith("/dashboard/knowledge/created-kb");
    }
  });
});
