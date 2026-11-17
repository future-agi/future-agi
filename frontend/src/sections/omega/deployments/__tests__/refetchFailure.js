import { act } from "@testing-library/react";
import { expect } from "vitest";

import { OmegaError } from "src/sections/omega/shared/omegaStates";

export const refetchErrors = [
  new OmegaError({ state: "error", status: 200, code: "malformed_response" }),
  new OmegaError({ state: "unavailable", status: 503, code: "network_error" }),
];

/** Refetch a successful query through the real client, without retry delays. */
export async function failRefetch(queryClient, queryKey, request, error) {
  const before = queryClient.getQueryState(queryKey);
  expect(before.status).toBe("success");
  expect(request).toHaveBeenCalledTimes(1);
  request.mockRejectedValue(error);
  await act(async () => {
    // fetchQuery forces the stale query to refetch; explicit options override
    // the hook's retryRead policy without mocking the query or its cache.
    await expect(
      queryClient.fetchQuery({
        queryKey,
        queryFn: request,
        staleTime: 0,
        retry: false,
      }),
    ).rejects.toBe(error);
    // Flush React Query's scheduled observer notification before checking UI.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  expect(request).toHaveBeenCalledTimes(2);
  expect(queryClient.getQueryState(queryKey)).toMatchObject({
    status: "error",
    fetchStatus: "idle",
    error,
  });
  expect(queryClient.getQueryData(queryKey)).toBe(before.data);
}
