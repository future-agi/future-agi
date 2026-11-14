import React from "react";
import PropTypes from "prop-types";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import {
  annotationQueueEndpoints,
  downloadStoredExport,
  useDownloadAnnotationQueueExport,
  waitForExportJob,
} from "../annotation-queues";

vi.mock("src/utils/axios", () => ({ default: { get: vi.fn() } }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

function Wrapper({ children }) {
  const [client] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: { mutations: { retry: false } },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Wrapper.propTypes = { children: PropTypes.node };

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("background annotation exports", () => {
  it("uses the generated job route and encodes both identifiers", () => {
    expect(annotationQueueEndpoints.exportJob("queue/a", "job/b")).toBe(
      "/model-hub/annotation-queues/queue%2Fa/export-jobs/job%2Fb/",
    );
  });

  it("keeps polling during retries and returns the completed file", async () => {
    vi.useFakeTimers();
    const job = {
      status: "succeeded",
      file_url: "https://storage.invalid/export.json",
    };
    const client = {
      get: vi
        .fn()
        .mockResolvedValueOnce({ data: { result: { status: "running" } } })
        .mockResolvedValueOnce({ data: { result: job } }),
    };
    const pending = waitForExportJob(client, "queue-1", "job-1");
    await vi.advanceTimersByTimeAsync(2000);
    expect(await pending).toEqual(job);
    expect(client.get).toHaveBeenCalledTimes(2);
    expect(client.get).toHaveBeenLastCalledWith(
      "/model-hub/annotation-queues/queue-1/export-jobs/job-1/",
    );
  });

  it("surfaces the final job failure", async () => {
    const client = {
      get: vi.fn().mockResolvedValue({
        data: {
          result: { status: "failed", error: "Export storage is unavailable" },
        },
      }),
    };
    await expect(waitForExportJob(client, "q", "j")).rejects.toThrow(
      "Export storage is unavailable",
    );
  });

  it("the queue download hook polls a real contract route after 202", async () => {
    const downloadUrl =
      "/model-hub/annotation-queues/queue-1/export-jobs/job-1/download/?token=signed";
    const file = new Blob(['[{"private":"synthetic"}]']);
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = vi.fn(() => "blob:private-export");
        static revokeObjectURL = vi.fn();
      },
    );
    axios.get
      .mockResolvedValueOnce({ status: 202, data: { job_id: "job-1" } })
      .mockResolvedValueOnce({
        data: {
          result: {
            status: "succeeded",
            file_url: downloadUrl,
            file_name: "annotations.json",
          },
        },
      })
      .mockResolvedValueOnce({ data: file });
    const clicks = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function click() {
        clicks.push({ href: this.href, download: this.download });
      },
    );
    const { result } = renderHook(() => useDownloadAnnotationQueueExport(), {
      wrapper: Wrapper,
    });
    result.current.mutate({ queueId: "queue-1" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenNthCalledWith(
      2,
      "/model-hub/annotation-queues/queue-1/export-jobs/job-1/",
    );
    expect(axios.get).toHaveBeenNthCalledWith(3, downloadUrl, {
      responseType: "blob",
    });
    expect(URL.createObjectURL).toHaveBeenCalledWith(file);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:private-export");
    expect(clicks).toEqual([
      {
        href: "blob:private-export",
        download: "annotations.json",
      },
    ]);
  });

  it.each([
    "https://storage.invalid/export.json",
    "//storage.invalid/export.json",
  ])("never sends credentials to a storage URL: %s", async (fileUrl) => {
    const client = { get: vi.fn() };
    await expect(
      downloadStoredExport(client, { file_url: fileUrl }, "export.json"),
    ).rejects.toThrow("Invalid export download URL");
    expect(client.get).not.toHaveBeenCalled();
  });
});
