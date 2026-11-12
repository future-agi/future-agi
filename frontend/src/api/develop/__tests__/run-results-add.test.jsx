// results-add-to-dataset: the copy request goes through the endpoint
// registry to datasets/{id | new}/rows/from-results/ with the target id kept
// out of the body, is never repeated on its own (a repeat can add the rows
// twice) and leaves saying its failures to the dialog. Errors have the shape
// the app's client rejects with (src/utils/axios): the answer's body spread
// out plus `statusCode`, with no `response`.
import { beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import PropTypes from "prop-types";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, renderHook, waitFor } from "src/utils/test-utils";

const h = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: h.post },
}));

import { useAddResultsToDataset } from "../run-results";

const withClient = (client) => {
  function Providers({ children }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  Providers.propTypes = { children: PropTypes.node };
  return Providers;
};

// No answer at all: no status, only the client's own words.
const noAnswer = () => ({
  message: "Network Error",
  statusCode: undefined,
  transportCode: "ERR_NETWORK",
  responseHeaders: {},
});

const runFailedCopy = async (rejection) => {
  h.post.mockRejectedValue(rejection);
  const client = new QueryClient();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const onError = vi.fn();
  const { result } = renderHook(
    () => useAddResultsToDataset("GS", { onError }),
    { wrapper: withClient(client) },
  );
  act(() => {
    result.current.mutate({ targetId: "new", mode: "copy" });
  });
  await waitFor(() => expect(result.current.isError).toBe(true));
  return { invalidate, onError };
};

beforeEach(() => {
  h.post.mockReset();
});

describe("useAddResultsToDataset (rows/from-results)", () => {
  it("posts a new-dataset copy to the new route without the target id in the body", async () => {
    h.post.mockResolvedValue({
      status: 200,
      data: { status: true, result: { rows_added: 1 } },
    });
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const onSuccess = vi.fn();
    const { result } = renderHook(
      () => useAddResultsToDataset("GS", { onSuccess }),
      { wrapper: withClient(client) },
    );
    act(() => {
      result.current.mutate({
        targetId: "new",
        mode: "copy",
        example_ids: ["e1"],
      });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.post).toHaveBeenCalledExactlyOnceWith(
      "/model-hub/datasets/new/rows/from-results/",
      { mode: "copy", example_ids: ["e1"] },
    );
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["develop"] });
  });

  it("never repeats a failed copy on its own and leaves the error to its caller", async () => {
    h.post.mockRejectedValue(noAnswer());
    const onCacheError = vi.fn();
    const client = new QueryClient({
      mutationCache: new MutationCache({ onError: onCacheError }),
      defaultOptions: { mutations: { retry: 2, retryDelay: 0 } },
    });
    const onError = vi.fn();
    const { result } = renderHook(
      () => useAddResultsToDataset("GS", { onError }),
      { wrapper: withClient(client) },
    );
    act(() => {
      result.current.mutate({ targetId: "RP", mode: "copy" });
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.post).toHaveBeenCalledWith(
      "/model-hub/datasets/RP/rows/from-results/",
      { mode: "copy" },
    );
    expect(onError).toHaveBeenCalledTimes(1);
    // The app-wide mutation handler skips errors the caller says itself.
    expect(onCacheError).toHaveBeenCalledTimes(1);
    expect(onCacheError.mock.calls[0][3].options.meta).toMatchObject({
      errorHandled: true,
    });
  });

  it.each([
    ["no answer", noAnswer()],
    [
      "a 504",
      {
        message: "Request failed with status code 504",
        statusCode: 504,
        transportCode: "ERR_BAD_RESPONSE",
        responseHeaders: {},
      },
    ],
  ])(
    "%s: the rows or a new dataset may exist, so the dataset lists are read again",
    async (_name, rejection) => {
      const { invalidate, onError } = await runFailedCopy(rejection);
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["develop"] });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["datasets"] });
      expect(onError).toHaveBeenCalledTimes(1);
      expect(onError.mock.calls[0][0]).toBe(rejection);
    },
  );

  it("a refusal adds nothing, so the dataset lists are not read again", async () => {
    const rejection = {
      status: false,
      detail: "A dataset with this name already exists. Choose another name.",
      statusCode: 400,
      transportCode: "ERR_BAD_REQUEST",
      responseHeaders: {},
    };
    const { invalidate, onError } = await runFailedCopy(rejection);
    expect(invalidate).not.toHaveBeenCalled();
    expect(onError.mock.calls[0][0]).toBe(rejection);
  });
});
