import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: vi.fn() },
}));

const axios = (await import("src/utils/axios")).default;
const { useCancelExecution } = await import("../useCancelExecution");

// The app-wide toast reads `mutation.options.meta.errorHandled` from the
// mutation cache's onError, so that is what these tests observe.
const failedMutationMeta = async (options) => {
  const onError = vi.fn();
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError }),
    defaultOptions: { mutations: { retry: false } },
  });
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => useCancelExecution(options), { wrapper });
  result.current.mutate("ex-1");
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(onError).toHaveBeenCalledTimes(1);
  return onError.mock.calls[0][3].options.meta;
};

describe("useCancelExecution", () => {
  beforeEach(() => {
    axios.post.mockReset();
    axios.post.mockRejectedValue({ statusCode: 409, result: "refused" });
  });

  it("keeps the app-wide error toast for a caller that shows no error of its own", async () => {
    expect((await failedMutationMeta())?.errorHandled).toBeUndefined();
  });

  it("silences the app-wide error toast for a caller that shows its own", async () => {
    expect(
      (await failedMutationMeta({ errorHandled: true }))?.errorHandled,
    ).toBe(true);
  });
});
