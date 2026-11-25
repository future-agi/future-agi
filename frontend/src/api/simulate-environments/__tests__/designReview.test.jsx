import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: vi.fn() },
}));

const axios = (await import("src/utils/axios")).default;
const { designReviewKey, useApproveDesign } = await import("../designReview");

const REVISION = "a".repeat(64);

const makeWrapper = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
};

describe("useApproveDesign", () => {
  beforeEach(() => {
    axios.post.mockReset();
  });

  it("approves the revision on screen", async () => {
    axios.post.mockResolvedValue({ data: { status: "approved" } });
    const { wrapper } = makeWrapper();

    const { result } = renderHook(() => useApproveDesign("env-1"), { wrapper });
    result.current.mutate(REVISION);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/env-1/design-review/approve/",
      { revision: REVISION },
    );
  });

  it("reads the design again when the server refuses a stale one", async () => {
    axios.post.mockRejectedValue({ statusCode: 409 });
    const { client, wrapper } = makeWrapper();
    const spy = vi.spyOn(client, "invalidateQueries");

    const { result } = renderHook(() => useApproveDesign("env-1"), { wrapper });
    result.current.mutate(REVISION);

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(spy).toHaveBeenCalledWith({ queryKey: designReviewKey("env-1") });
  });

  it("leaves the design alone on other refusals", async () => {
    axios.post.mockRejectedValue({ statusCode: 503 });
    const { client, wrapper } = makeWrapper();
    const spy = vi.spyOn(client, "invalidateQueries");

    const { result } = renderHook(() => useApproveDesign("env-1"), { wrapper });
    result.current.mutate(REVISION);

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(spy).not.toHaveBeenCalled();
  });
});
