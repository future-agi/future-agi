/* eslint-disable react/prop-types */
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

const mocks = vi.hoisted(() => ({
  delete: vi.fn(),
  notify: vi.fn(),
  closeSnackbar: vi.fn(),
  navigate: vi.fn(),
  onSuccess: null,
  clearUpload: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { delete: mocks.delete },
  endpoints: { develop: { deleteDataset: () => "/datasets/delete/" } },
}));
vi.mock("../useFileUploadLifecycle", () => ({
  useFileUploadLifecycle: ({ onSuccess }) => {
    mocks.onSuccess = onSuccess;
    return { isPending: false, clearUpload: mocks.clearUpload };
  },
}));
vi.mock("react-router", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("src/utils/prototype", () => ({
  isPrototypeMode: () => false,
  prototypeModeFromEnv: () => false,
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: mocks.notify }));
vi.mock("notistack", () => ({ closeSnackbar: mocks.closeSnackbar }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/hook-form", () => ({ RHFUpload: () => null }));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: () => null,
}));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));
vi.mock("src/sections/develop-detail/Common/HelperText", () => ({
  default: () => null,
}));
vi.mock("../UploadedFileIllustration", () => ({ default: () => null }));
vi.mock("../ImportPreviewPane", () => ({ default: () => null }));
vi.mock("../ImportFileExtras", () => ({ default: () => null }));

import UploadFileModal from "../UploadFileModal";

const DATASET_ID = "original-dataset";
const DATASET_NAME = "Import test";
const ERROR_SNACKBAR_KEY = `dataset-created-undo-error:${DATASET_ID}#1`;
const DETAIL_KEY = ["get-dataset-detail", DATASET_ID];
const DATASET = { id: DATASET_ID, name: DATASET_NAME };
const FAILURES = [
  ["offline", new Error("Network Error")],
  [
    "403",
    {
      response: {
        status: 403,
        data: {
          code: "permission_denied",
          detail: "You cannot delete this dataset.",
        },
      },
    },
  ],
  [
    "500",
    { response: { status: 500, data: { message: "Internal server failure" } } },
  ],
];
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
const removedMessages = () =>
  mocks.notify.mock.calls.filter(([message]) => message.startsWith("Removed"));
const errorToasts = () =>
  mocks.notify.mock.calls.filter(([, options]) => options.variant === "error");
let client;
let invalidate;
let closeError;

beforeEach(() => {
  vi.resetAllMocks();
  closeError = vi.fn();
  mocks.notify.mockImplementation(() => ({
    undone: Promise.resolve(false),
    close: closeError,
  }));
  mocks.delete.mockResolvedValue({ data: {} });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  invalidate = vi.spyOn(client, "invalidateQueries");
  client.setQueryData(DETAIL_KEY, DATASET);
  client.setQueryData(["datasets", "list"], [DATASET]);
  render(
    <QueryClientProvider client={client}>
      <UploadFileModal open={false} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.restoreAllMocks();
});

async function completeUpload(id = DATASET_ID, name = DATASET_NAME) {
  const undo = deferred();
  mocks.notify.mockReturnValueOnce({ undone: undo.promise, close: vi.fn() });
  const form = new FormData();
  form.set("new_dataset_name", name);
  await act(async () => {
    mocks.onSuccess({ data: { result: { dataset_id: id, rows: 1 } } }, form);
  });
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith(
      `/dashboard/develop/${id}?tab=data`,
    ),
  );
  expect(mocks.notify).toHaveBeenCalledWith(
    `Created "${name}" · 1 row`,
    expect.objectContaining({ variant: "success" }),
  );
  mocks.navigate.mockClear();
  invalidate.mockClear();
  return undo;
}

async function requestUndo(undo) {
  await act(async () => {
    undo.resolve(true);
  });
}

function expectCreatedDatasetRetained() {
  expect(removedMessages()).toHaveLength(0);
  expect(mocks.navigate).not.toHaveBeenCalled();
  expect(invalidate).not.toHaveBeenCalled();
  expect(client.getQueryData(DETAIL_KEY)).toEqual(DATASET);
  expect(client.getQueryData(["datasets", "list"])).toEqual([DATASET]);
}

async function failUndo(error) {
  mocks.delete.mockRejectedValueOnce(error);
  const undo = await completeUpload();
  await requestUndo(undo);
  // Regression: the old empty catch announces Removed and navigates here.
  expectCreatedDatasetRetained();
  expect(errorToasts()).toHaveLength(1);
  const [message, options] = errorToasts()[0];
  expect(message).toEqual(expect.any(String));
  expect(message.length).toBeGreaterThan(0);
  expect(options.persist).toBe(true);
  render(options.action(ERROR_SNACKBAR_KEY));
}

describe("created dataset Undo", () => {
  it("announces removal and navigates only after DELETE succeeds", async () => {
    const deletion = deferred();
    mocks.delete.mockReturnValueOnce(deletion.promise);
    const undo = await completeUpload();
    await requestUndo(undo);
    expect(mocks.delete).toHaveBeenCalledWith("/datasets/delete/", {
      data: { dataset_ids: [DATASET_ID] },
    });
    expectCreatedDatasetRetained();
    await act(async () => {
      deletion.resolve({ data: {} });
    });
    expect(mocks.navigate).toHaveBeenCalledExactlyOnceWith(
      "/dashboard/develop",
    );
    expect(removedMessages()).toHaveLength(1);
    expect(removedMessages()[0][0]).toBe('Removed "Import test"');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["datasets", "list"] });
    expect(errorToasts()).toHaveLength(0);
  });

  it("does not delete when the Undo window closes without activation", async () => {
    const undo = await completeUpload();
    await act(async () => {
      undo.resolve(false);
    });
    expect(mocks.delete).not.toHaveBeenCalled();
    expectCreatedDatasetRetained();
  });

  it.each(FAILURES.map(([label, error]) => ({ label, error })))(
    "keeps the created dataset after $label and offers retry and open actions",
    async ({ error }) => {
      await failUndo(error);
      expect(
        screen.getByRole("button", { name: "Retry removal" }),
      ).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: "Open dataset" }));
      expect(mocks.navigate).toHaveBeenCalledExactlyOnceWith(
        `/dashboard/develop/${DATASET_ID}?tab=data`,
      );
      expect(mocks.delete).toHaveBeenCalledOnce();
      expect(removedMessages()).toHaveLength(0);
    },
  );

  it.each(FAILURES.map(([label, error]) => ({ label, error })))(
    "dismisses the persistent error after $label without another DELETE",
    async ({ error }) => {
      await failUndo(error);
      expect(mocks.closeSnackbar).not.toHaveBeenCalled();
      expect(mocks.delete).toHaveBeenCalledOnce();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
      });
      expect(mocks.closeSnackbar).toHaveBeenCalledExactlyOnceWith(
        ERROR_SNACKBAR_KEY,
      );
      expect(mocks.delete).toHaveBeenCalledOnce();
      expectCreatedDatasetRetained();
    },
  );

  it.each(FAILURES.map(([label, error]) => ({ label, error })))(
    "retries the original dataset after $label, even after another upload and unmount",
    async ({ error }) => {
      await failUndo(error);
      const retry = screen.getByRole("button", { name: "Retry removal" });
      await completeUpload("later-dataset", "Later upload");
      // Route navigation can unmount the uploader; the toast action must survive.
      const retryAction = errorToasts()[0][1].action;
      expect(retry).toBeEnabled();
      cleanup();
      render(retryAction());
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Retry removal" }));
      });
      expect(mocks.delete.mock.calls).toEqual([
        ["/datasets/delete/", { data: { dataset_ids: [DATASET_ID] } }],
        ["/datasets/delete/", { data: { dataset_ids: [DATASET_ID] } }],
      ]);
      expect(removedMessages()).toHaveLength(1);
      expect(removedMessages()[0][0]).toBe('Removed "Import test"');
      expect(mocks.navigate).toHaveBeenCalledExactlyOnceWith(
        "/dashboard/develop",
      );
      expect(closeError).toHaveBeenCalledOnce();
    },
  );

  it("keeps a failed retry actionable and prevents duplicate pending or completed deletes", async () => {
    await failUndo(FAILURES[0][1]);
    mocks.delete.mockRejectedValueOnce(FAILURES[2][1]);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry removal" }));
    });
    expectCreatedDatasetRetained();
    expect(errorToasts()).toHaveLength(2);
    expect(errorToasts()[1][1].key).toBe(errorToasts()[0][1].key);
    cleanup();
    render(errorToasts()[1][1].action());
    const retry = screen.getByRole("button", { name: "Retry removal" });
    const deletion = deferred();
    mocks.delete.mockReturnValueOnce(deletion.promise);
    await act(async () => {
      fireEvent.click(retry);
      fireEvent.click(retry);
    });
    expect(mocks.delete).toHaveBeenCalledTimes(3);
    expectCreatedDatasetRetained();
    await act(async () => {
      deletion.resolve({ data: {} });
    });
    await act(async () => {
      fireEvent.click(retry);
    });
    expect(mocks.delete).toHaveBeenCalledTimes(3);
    expect(removedMessages()).toHaveLength(1);
  });
});
