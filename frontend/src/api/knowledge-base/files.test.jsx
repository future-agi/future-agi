import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PropTypes from "prop-types";
import { afterEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import { act, cleanup, renderHook, waitFor } from "src/utils/test-utils";

import { useDownloadKnowledgeBaseFile } from "./files";

function renderDownload() {
  const client = new QueryClient();
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node.isRequired };
  return renderHook(() => useDownloadKnowledgeBaseFile(), { wrapper: Wrapper });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("useDownloadKnowledgeBaseFile", () => {
  it("requests the contracted attachment route and returns the exact blob", async () => {
    const blob = new Blob(["owned file bytes"], { type: "text/plain" });
    const get = vi.spyOn(axios, "get").mockResolvedValue({ data: blob });
    const { result } = renderDownload();
    let received;
    await act(async () => {
      received = await result.current.mutateAsync({ kbId: "collection-id", fileId: "file-id" });
    });
    expect(received).toBe(blob);
    expect(get).toHaveBeenCalledExactlyOnceWith(
      "/model-hub/kb/collection-id/download-file/",
      { params: { file_id: "file-id" }, responseType: "blob" },
    );
    await waitFor(() => expect(result.current.isPending).toBe(false));
  });

  it("propagates a refusal without retrying or returning attachment bytes", async () => {
    const refusal = new Error("Forbidden");
    const get = vi.spyOn(axios, "get").mockRejectedValue(refusal);
    const { result } = renderDownload();
    await act(async () => {
      await expect(result.current.mutateAsync({ kbId: "collection-id", fileId: "file-id" }))
        .rejects.toBe(refusal);
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
    expect(result.current.isPending).toBe(false);
    expect(get).toHaveBeenCalledTimes(1);
  });
});
