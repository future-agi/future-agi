import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({ delete: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    dataset: {
      deleteCompareDataset: (id) => `/model-hub/datasets/delete-compare/${id}/`,
    },
  },
}));

import { useDeleteCompare } from "../dataset-compare";

const COMPARE_ID = "00000000-0000-4000-8000-0000000000aa";

// The app's MutationCache onError toasts every failed mutation, so record what
// reaches it the same way.
function renderDeleteCompare() {
  const onError = vi.fn();
  const queryClient = new QueryClient({
    mutationCache: new MutationCache({ onError }),
  });
  function Wrapper({ children }) {
    return React.createElement(
      QueryClientProvider,
      { client: queryClient },
      children,
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  const { result } = renderHook(() => useDeleteCompare(), { wrapper: Wrapper });
  return { result, onError };
}

describe("useDeleteCompare", () => {
  beforeEach(() => {
    mocks.delete.mockReset();
  });

  it("deletes the comparison by id", async () => {
    mocks.delete.mockResolvedValue({ data: { status: true } });
    const { result, onError } = renderDeleteCompare();

    await act(async () => {
      await result.current.mutateAsync(COMPARE_ID);
    });

    expect(mocks.delete).toHaveBeenCalledExactlyOnceWith(
      `/model-hub/datasets/delete-compare/${COMPARE_ID}/`,
    );
    expect(onError).not.toHaveBeenCalled();
  });

  it("treats a comparison that is already gone as deleted", async () => {
    mocks.delete.mockRejectedValue({
      statusCode: 404,
      result: "This comparison was not found or has expired.",
    });
    const { result, onError } = renderDeleteCompare();

    let value;
    await act(async () => {
      value = await result.current.mutateAsync(COMPARE_ID);
    });

    expect(value).toBeNull();
    expect(onError).not.toHaveBeenCalled();
  });

  it("still reports other delete failures", async () => {
    const failure = { statusCode: 500, result: "Something went wrong" };
    mocks.delete.mockRejectedValue(failure);
    const { result, onError } = renderDeleteCompare();

    await act(async () => {
      await expect(result.current.mutateAsync(COMPARE_ID)).rejects.toBe(
        failure,
      );
    });

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toBe(failure);
  });
});
