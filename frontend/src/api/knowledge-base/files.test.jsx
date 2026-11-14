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
  vi.unstubAllEnvs();
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

  it.each([
    ["text/plain", "owned file bytes"],
    ["application/pdf", "%PDF-1.4 synthetic"],
  ])(
    "returns a %s attachment through the response contract check in strict mode",
    async (contentType, contents) => {
      vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", "true");
      const blob = new Blob([contents], { type: contentType });
      const adapter = vi.fn(async (config) => ({
        data: blob,
        status: 200,
        statusText: "OK",
        headers: { "content-type": contentType },
        config,
      }));
      vi.spyOn(axios.defaults, "adapter", "get").mockReturnValue(adapter);
      const { result } = renderDownload();
      let received;
      await act(async () => {
        received = await result.current.mutateAsync({
          kbId: "3f8b1c2e-7d4a-4e9b-9c1a-2b3c4d5e6f70",
          fileId: "7a6b5c4d-3e2f-4a1b-8c9d-0e1f2a3b4c5d",
        });
      });
      expect(received).toBe(blob);
      expect(adapter).toHaveBeenCalledTimes(1);
      expect(adapter.mock.calls[0][0]).toMatchObject({
        url: "/model-hub/kb/3f8b1c2e-7d4a-4e9b-9c1a-2b3c4d5e6f70/download-file/",
        params: { file_id: "7a6b5c4d-3e2f-4a1b-8c9d-0e1f2a3b4c5d" },
        responseType: "blob",
      });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
    },
  );
});
