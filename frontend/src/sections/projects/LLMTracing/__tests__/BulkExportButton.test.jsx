import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import BulkExportButton from "../BulkExportButton";
import { getRequestedTraceAttributeKeys } from "../traceAttributeRequest";

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post, get },
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  post.mockReset();
  get.mockReset();
});

const expiredUrl = "https://downloads.example.test/export.csv?signature=expired";
const freshUrl = "https://downloads.example.test/export.csv?signature=fresh";

async function completeExportAndExpireLink(jobId = "job") {
  vi.useFakeTimers();
  post.mockResolvedValueOnce({ data: { id: jobId, status: "queued" } });
  get.mockResolvedValueOnce({
    data: { id: jobId, status: "completed", download_url: expiredUrl },
  });
  render(<BulkExportButton projectId="project" rowType="traces" filters={[]} />);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Export all rows" }));
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(screen.getByText("Download export")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(16 * 60 * 1000); });
  expect(get).toHaveBeenCalledTimes(1);
}

describe("BulkExportButton signed downloads", () => {
  it("encodes the job id for both polling and refreshed downloads", async () => {
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const jobId = "job /?#";
    const statusPath = "/tracer/bulk-exports/job%20%2F%3F%23/";
    await completeExportAndExpireLink(jobId);
    expect(get).toHaveBeenNthCalledWith(1, statusPath);
    get.mockResolvedValueOnce({
      data: { id: jobId, status: "completed", download_url: freshUrl },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Download export" }));
    });
    expect(get).toHaveBeenCalledTimes(2);
    expect(get).toHaveBeenNthCalledWith(2, statusPath);
    expect(click).toHaveBeenCalledTimes(1);
    expect(click.mock.instances[0].href).toBe(freshUrl);
  });

  it("fetches and opens a fresh signed URL after the first link expires", async () => {
    const downloads = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function download() {
      downloads.push(this.href);
    });
    await completeExportAndExpireLink();
    let resolveStatus;
    get.mockImplementationOnce(() => new Promise((resolve) => { resolveStatus = resolve; }));
    await act(async () => { fireEvent.click(screen.getByText("Download export")); });
    expect(get).toHaveBeenCalledTimes(2);
    expect(get).toHaveBeenLastCalledWith("/tracer/bulk-exports/job/");
    expect(click).not.toHaveBeenCalled();
    await act(async () => {
      resolveStatus({ data: { id: "job", status: "completed", download_url: freshUrl } });
    });
    expect(click).toHaveBeenCalledTimes(1);
    expect(downloads).toEqual([freshUrl]);
  });

  it.each(["request fails", "URL unavailable"])("does not open the stale URL when %s", async (failure) => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await completeExportAndExpireLink();
    if (failure === "request fails") get.mockRejectedValueOnce(new Error("unavailable"));
    else get.mockResolvedValueOnce({ data: { id: "job", status: "completed", download_url: null } });
    await act(async () => { fireEvent.click(screen.getByText("Download export")); });
    expect(get).toHaveBeenCalledTimes(2);
    expect(click).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not download export. Please retry.");
  });
});

describe("BulkExportButton projection", () => {
  it("sends the displayed trace attributes, including keys with commas", async () => {
    post.mockResolvedValue({ data: { id: "job", status: "failed" } });
    const keys = getRequestedTraceAttributeKeys([
      { id: "trace_name", groupBy: "System" },
      { id: "metadata.path,with-comma", groupBy: "Custom Columns" },
      { id: "hidden", groupBy: "Custom Columns", isVisible: false },
    ]);
    render(<BulkExportButton projectId="project" rowType="traces" filters={[]} attributeKeys={keys} />);
    fireEvent.click(screen.getByRole("button", { name: "Export all rows" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/tracer/bulk-exports/", {
      project_id: "project", row_type: "traces", format: "csv",
      filters: { filters: [], attribute_keys: ["metadata.path,with-comma"] },
    }));
  });
});
