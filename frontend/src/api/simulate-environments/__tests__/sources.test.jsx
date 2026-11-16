import { beforeEach, describe, expect, it, vi } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

const { default: axios } = await import("src/utils/axios");
const {
  useAcceptSource,
  useEnvironmentSources,
  usePrepareKbSource,
  usePrepareUploadSource,
} = await import("../sources");

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
wrapper.propTypes = { children: PropTypes.node };

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  axios.get.mockReset();
  axios.post.mockReset();
});

describe("Environment source API", () => {
  it("reads the safe summary for one Environment", async () => {
    axios.get.mockResolvedValueOnce({ data: { sources: [] } });
    const { result } = renderHook(() => useEnvironmentSources("env-1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.get).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/sources/",
    );
  });

  it("prepares an authorized Knowledge Base selection", async () => {
    axios.post.mockResolvedValueOnce({ data: { import_ref: "signed" } });
    const { result } = renderHook(() => usePrepareKbSource("env-1"), { wrapper });
    const body = { selection: { kind: "knowledge_base", kb_id: "kb-1" } };

    await act(() => result.current.mutateAsync(body));
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/sources/imports/prepare/",
      body,
    );
  });

  it("sends uploads as multipart data with the prepared draft basis", async () => {
    axios.post.mockResolvedValueOnce({ data: { import_ref: "signed" } });
    const { result } = renderHook(() => usePrepareUploadSource("env-1"), {
      wrapper,
    });
    const file = new File(["id,name\n1,Asha"], "customers.csv", {
      type: "text/csv",
    });

    await act(() =>
      result.current.mutateAsync({
        files: [file],
        purpose: "seed_records",
        versionId: "version-1",
        draft: { id: "draft-1", revision: 4 },
      }),
    );
    const [, body] = axios.post.mock.calls[0];
    expect(axios.post.mock.calls[0][0]).toBe(
      "/simulate/api/harness-environments/env-1/sources/uploads/prepare/",
    );
    expect(body.getAll("files")).toEqual([file]);
    expect(body.get("purpose")).toBe("seed_records");
    expect(body.get("expected_environment_version_id")).toBe("version-1");
    expect(body.get("expected_draft_id")).toBe("draft-1");
    expect(body.get("expected_draft_revision")).toBe("4");
  });

  it("accepts the signed review into the draft", async () => {
    axios.post.mockResolvedValueOnce({ data: { draft: { revision: 1 } } });
    const { result } = renderHook(() => useAcceptSource("env-1"), { wrapper });
    const body = { import_ref: "signed", review: { reference_reviews: [] } };

    await act(() => result.current.mutateAsync(body));
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/sources/imports/accept/",
      body,
    );
  });
});
