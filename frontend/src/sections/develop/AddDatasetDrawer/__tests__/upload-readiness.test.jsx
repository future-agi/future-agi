/* eslint-disable react/prop-types */
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
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Controller } from "react-hook-form";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  notify: vi.fn(),
  navigate: vi.fn(),
  close: vi.fn(),
  refresh: vi.fn(),
  filename: "data.csv",
  content: "input\nhello\n",
}));
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post, get: mocks.get },
  endpoints: { develop: { uploadDatasetLocalFile: "/upload/" } },
}));
vi.mock("react-router", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("src/utils/prototype", () => ({
  isPrototypeMode: () => false,
  prototypeModeFromEnv: () => false,
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: mocks.notify }));
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
vi.mock("../UploadedFileIllustration", () => ({ default: () => null }));
vi.mock("../ImportFileExtras", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));
vi.mock("src/sections/develop-detail/Common/HelperText", () => ({
  default: () => null,
}));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: ({ control, fieldName, label }) => (
    <Controller
      name={fieldName}
      control={control}
      render={({ field }) => <input aria-label={label} {...field} />}
    />
  ),
}));
vi.mock("src/components/hook-form", () => ({
  RHFUpload: ({ onDrop }) => (
    <button
      onClick={() => {
        const file = new File([mocks.content], mocks.filename);
        file.text = async () => mocks.content;
        onDrop([file]);
      }}
    >
      Choose fixture
    </button>
  ),
}));

import UploadFileModal from "../UploadFileModal";

let client;
let progress;
beforeEach(() => {
  vi.clearAllMocks();
  mocks.filename = "data.csv";
  mocks.content = "input\nhello\n";
  progress = { processing_status: "processing", is_processing: true };
  mocks.post.mockResolvedValue({
    data: { result: { dataset_id: "dataset-1", processing_status: "queued" } },
  });
  mocks.get.mockImplementation(async () => ({ data: { result: progress } }));
  mocks.notify.mockReturnValue({ undone: Promise.resolve(false) });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

async function startUpload() {
  const view = render(
    <QueryClientProvider client={client}>
      <UploadFileModal open onClose={mocks.close} refreshGrid={mocks.refresh} />
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByLabelText("Name"), {
    target: { value: "Import test" },
  });
  fireEvent.click(screen.getByText("Choose fixture"));
  const submit = await screen.findByRole("button", { name: "Import 1 row" });
  await waitFor(() => expect(submit).not.toBeDisabled());
  fireEvent.click(submit);
  await waitFor(() => expect(mocks.post).toHaveBeenCalledOnce());
  return view;
}

const createdMessages = () =>
  mocks.notify.mock.calls.filter(([message]) => message.startsWith("Created"));

describe("upload completion lifecycle", () => {
  it("keeps the upload pending through automatic retries and completes once", async () => {
    progress = {
      processing_status: "retrying",
      is_processing: true,
      is_completed: false,
      is_failed: false,
    };
    await startUpload();
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toBe(
        "Import interrupted. Retrying automatically.",
      ),
    );
    expect(screen.getByLabelText("Name")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import 1 row" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Import 1 row" }));
    expect(mocks.post).toHaveBeenCalledTimes(1);
    const query = client.getQueryCache().find({
      queryKey: ["dataset-creation-progress", "dataset-1"],
    });
    expect(query.options.refetchInterval(query)).toBe(1000);
    expect(createdMessages()).toHaveLength(0);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(mocks.close).toHaveBeenCalledTimes(0);
    expect(mocks.navigate).toHaveBeenCalledTimes(0);

    progress = { processing_status: "completed", is_completed: true };
    await client.invalidateQueries({
      queryKey: ["dataset-creation-progress", "dataset-1"],
    });
    await waitFor(() => expect(createdMessages()).toHaveLength(1));
    await waitFor(() => expect(mocks.navigate).toHaveBeenCalledTimes(1));
    expect(mocks.navigate).toHaveBeenCalledWith(
      "/dashboard/develop/dataset-1?tab=data",
    );
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.close).toHaveBeenCalledTimes(1);
  });

  it("blocks malformed CSV before sending the original file", async () => {
    mocks.content = "id,name\n1,a\nlonely\n2,b\n";
    render(
      <QueryClientProvider client={client}>
        <UploadFileModal open onClose={mocks.close} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText("Choose fixture"));
    const submit = await screen.findByRole("button", { name: "Import 2 rows" });

    expect(submit).toBeDisabled();
    expect(screen.getByTestId("upload-problem").textContent).toBe(
      "CSV rows have inconsistent column counts. Correct the file and upload it again; no rows will be imported.",
    );
    expect(
      screen.getByText(
        "No rows will be imported. Correct the malformed lines and upload again.",
      ).textContent,
    ).toBe(
      "No rows will be imported. Correct the malformed lines and upload again.",
    );
    expect(screen.queryByText(/will be skipped/)).toBeNull();
    fireEvent.click(submit);
    expect(mocks.post).toHaveBeenCalledTimes(0);
  });

  it("imports ordinary and smart-quoted CSV rows", async () => {
    mocks.content = "id,name\n1,plain\n2,“b,c”\n";
    render(
      <QueryClientProvider client={client}>
        <UploadFileModal open onClose={mocks.close} />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Quoted records" },
    });
    fireEvent.click(screen.getByText("Choose fixture"));
    const submit = await screen.findByRole("button", { name: "Import 2 rows" });
    await waitFor(() => expect(submit).toBeEnabled());

    const preview = within(screen.getByRole("table", { name: "File preview" }));
    const cells = preview.getAllByRole("cell").map((cell) => cell.textContent);
    expect(cells).toEqual(["1", "1", "plain", "2", "2", "b,c"]);
    expect(screen.queryByTestId("upload-problem")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/will be skipped/)).toBeNull();
    fireEvent.click(submit);
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    const file = mocks.post.mock.calls[0][1].get("file");
    expect(file.name).toBe("data.csv");
    expect(file.size).toBe(new Blob([mocks.content]).size);
    expect(await file.text()).toBe(mocks.content);
  });

  it("still imports readable JSONL records and explains skipped lines", async () => {
    mocks.filename = "records.jsonl";
    mocks.content = '{"input":"hello"}\nnot a record\n';
    await startUpload();

    expect(
      screen.getByText("1 line can't be read and will be skipped").textContent,
    ).toBe("1 line can't be read and will be skipped");
    const form = mocks.post.mock.calls[0][1];
    expect(form.get("file").name).toBe("records.jsonl");
    expect(form.get("file").size).toBe(mocks.content.length);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });

  it("does not announce Created for queued acceptance; polls and refetches only after completion", async () => {
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await startUpload();
    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith(
        "/model-hub/develops/dataset-creation-progress/dataset-1/",
        expect.anything(),
      ),
    );
    expect(createdMessages()).toHaveLength(0);
    expect(mocks.close).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent(/import/i);
    progress = { processing_status: "completed", is_completed: true };
    await client.invalidateQueries();
    await waitFor(() => expect(createdMessages()).toHaveLength(1));
    expect(mocks.refresh).toHaveBeenCalledOnce();
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["develop", "dataset-list"],
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["datasets", "list"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["dataset-detail"] });
    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith(
        "/dashboard/develop/dataset-1?tab=data",
      ),
    );
  });

  it("retries progress without submitting a duplicate upload", async () => {
    mocks.get.mockRejectedValueOnce(new Error("temporary progress failure"));
    await startUpload();
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Could not check import progress",
      ),
    );
    expect(createdMessages()).toHaveLength(0);
    progress = { processing_status: "completed", is_completed: true };
    fireEvent.click(
      screen.getByRole("button", { name: "Check progress again" }),
    );
    await waitFor(() => expect(createdMessages()).toHaveLength(1));
    expect(mocks.post).toHaveBeenCalledOnce();
  });

  it("keeps pending media visible instead of announcing success", async () => {
    progress = {
      processing_status: "completed",
      media_dispatch_status: "pending",
    };
    await startUpload();
    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/media/i),
    );
    expect(createdMessages()).toHaveLength(0);
    expect(mocks.close).not.toHaveBeenCalled();
  });

  it("shows media failure and can recheck an operator retry without reupload", async () => {
    progress = {
      processing_status: "completed",
      media_dispatch_status: "failed",
      media_processing_message: "Media processing failed",
    };
    await startUpload();
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Media processing failed",
      ),
    );
    expect(createdMessages()).toHaveLength(0);
    expect(mocks.close).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
    progress = {
      processing_status: "completed",
      media_dispatch_status: "dispatched",
    };
    fireEvent.click(
      screen.getByRole("button", { name: "Check progress again" }),
    );
    await waitFor(() => expect(createdMessages()).toHaveLength(1));
    expect(mocks.post).toHaveBeenCalledOnce();
  });

  it.each(["Cancel", "Close upload"])(
    "dismisses terminal media failure with %s without allowing a duplicate upload",
    async (closeButton) => {
      const invalidate = vi.spyOn(client, "invalidateQueries");
      const view = await startUpload();
      await waitFor(() => expect(mocks.get).toHaveBeenCalled());
      expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
      expect(
        screen.getByRole("button", { name: "Close upload" }),
      ).toBeDisabled();

      progress = {
        processing_status: "completed",
        media_dispatch_status: "failed",
      };
      await client.invalidateQueries({
        queryKey: ["dataset-creation-progress", "dataset-1"],
      });
      await waitFor(() =>
        expect(screen.getByRole("alert")).toHaveTextContent(
          "Media processing failed. Your table is saved. Ask an operator to retry media processing, then check progress again.",
        ),
      );
      expect(screen.getByRole("button", { name: "Cancel" })).not.toBeDisabled();
      expect(
        screen.getByRole("button", { name: "Close upload" }),
      ).not.toBeDisabled();
      expect(screen.getByLabelText("Name")).toBeDisabled();
      const submit = screen.getByRole("button", { name: "Import 1 row" });
      expect(submit).toBeDisabled();
      fireEvent.click(submit);
      expect(mocks.post).toHaveBeenCalledOnce();
      expect(screen.queryByText("Creating 1 row…")).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Check progress again" }),
      ).toBeEnabled();
      expect(createdMessages()).toHaveLength(0);
      expect(mocks.close).not.toHaveBeenCalled();
      expect(mocks.navigate).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("button", { name: closeButton }));
      expect(mocks.close).toHaveBeenCalledOnce();
      expect(mocks.refresh).toHaveBeenCalledOnce();
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["datasets", "list"],
      });
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["datasets", "query"],
      });
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["develop", "dataset-list"],
      });
      expect(createdMessages()).toHaveLength(0);
      expect(mocks.navigate).not.toHaveBeenCalled();

      // The real parent keeps the modal mounted when it closes.
      view.rerender(
        <QueryClientProvider client={client}>
          <UploadFileModal
            open={false}
            onClose={mocks.close}
            refreshGrid={mocks.refresh}
          />
        </QueryClientProvider>,
      );
      progress = {
        processing_status: "completed",
        media_dispatch_status: "dispatched",
      };
      await client.invalidateQueries({
        queryKey: ["dataset-creation-progress", "dataset-1"],
      });
      expect(createdMessages()).toHaveLength(0);
      expect(mocks.navigate).not.toHaveBeenCalled();
      view.rerender(
        <QueryClientProvider client={client}>
          <UploadFileModal
            open
            onClose={mocks.close}
            refreshGrid={mocks.refresh}
          />
        </QueryClientProvider>,
      );
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.getByLabelText("Name")).toBeEnabled();
      expect(
        screen.getByRole("button", { name: "Choose fixture" }),
      ).toBeEnabled();
      expect(mocks.post).toHaveBeenCalledOnce();
    },
  );

  it("does not claim success when upload acceptance already reports failed media", async () => {
    progress = {
      processing_status: "completed",
      media_dispatch_status: "failed",
    };
    mocks.post.mockResolvedValue({
      data: { result: { dataset_id: "dataset-1", ...progress } },
    });
    await startUpload();
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Media processing failed",
      ),
    );
    expect(createdMessages()).toHaveLength(0);
    expect(mocks.close).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Import 1 row" })).toBeDisabled();
    expect(mocks.post).toHaveBeenCalledOnce();
  });

  it("shows a failed import without closing or claiming success", async () => {
    progress = {
      processing_status: "failed",
      is_failed: true,
      error_message: "Could not write imported values",
    };
    await startUpload();
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Could not write imported values",
      ),
    );
    expect(createdMessages()).toHaveLength(0);
    expect(mocks.close).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });
});
