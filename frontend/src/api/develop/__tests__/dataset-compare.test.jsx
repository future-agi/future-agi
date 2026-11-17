import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({ delete: vi.fn(), post: vi.fn() }));
const snackbar = vi.hoisted(() => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    dataset: {
      deleteCompareDataset: (id) => `/model-hub/datasets/delete-compare/${id}/`,
      getCompareDatasetDownload: (id) =>
        `/model-hub/datasets/${id}/compare-datasets/download/`,
    },
  },
}));

vi.mock("src/components/snackbar", () => snackbar);

import {
  COMPARE_NOT_FOUND_CODE,
  COMPARE_STORAGE_UNAVAILABLE_CODE,
  COMPARE_STORAGE_UNAVAILABLE_MESSAGE,
  COMPARISON_EXPIRED_MESSAGE,
  useDeleteCompare,
  useDownloadCompare,
} from "../dataset-compare";

const COMPARE_ID = "00000000-0000-4000-8000-0000000000aa";

// The app's MutationCache onError toasts every failed mutation, so record what
// reaches it the same way.
function renderMutation(useHook) {
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
  const { result } = renderHook(useHook, { wrapper: Wrapper });
  return { result, onError };
}

function renderDeleteCompare() {
  return renderMutation(() => useDeleteCompare());
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

const LEFT_ID = "00000000-0000-4000-8000-000000000001";
const RIGHT_ID = "00000000-0000-4000-8000-000000000002";
const NEW_COMPARE_ID = "00000000-0000-4000-8000-0000000000bb";

function renderDownloadCompare(compareId, onComparisonExpired) {
  return renderMutation(() =>
    useDownloadCompare({
      compareId,
      selectedDatasetsValues: [LEFT_ID, RIGHT_ID],
      baseColumn: "question",
      onComparisonExpired,
    }),
  );
}

describe("useDownloadCompare", () => {
  beforeEach(() => {
    mocks.post.mockReset();
    snackbar.enqueueSnackbar.mockReset();
    window.URL.createObjectURL = vi.fn(() => "blob:compare");
    window.URL.revokeObjectURL = vi.fn();
  });

  it("downloads the comparison it was built for", async () => {
    mocks.post.mockResolvedValue({ data: "question,answer\n" });
    const onComparisonExpired = vi.fn();
    const { result, onError } = renderDownloadCompare(
      { current: COMPARE_ID },
      onComparisonExpired,
    );

    await act(async () => {
      await result.current.mutateAsync();
    });

    expect(mocks.post).toHaveBeenCalledExactlyOnceWith(
      `/model-hub/datasets/${LEFT_ID}/compare-datasets/download/`,
      {
        dataset_ids: [RIGHT_ID],
        base_column_name: "question",
        compare_id: COMPARE_ID,
      },
    );
    expect(window.URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(snackbar.enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
      "Dataset downloaded successfully",
      { variant: "success" },
    );
    expect(onComparisonExpired).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it("rebuilds an expired comparison instead of reporting a 404", async () => {
    mocks.post.mockRejectedValue({
      statusCode: 404,
      code: COMPARE_NOT_FOUND_CODE,
      result: "This comparison was not found or has expired.",
    });
    const onComparisonExpired = vi.fn();
    const { result, onError } = renderDownloadCompare(
      { current: COMPARE_ID },
      onComparisonExpired,
    );

    await act(async () => {
      await result.current.mutateAsync();
    });

    expect(onError).not.toHaveBeenCalled();
    expect(onComparisonExpired).toHaveBeenCalledTimes(1);
    expect(snackbar.enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
      COMPARISON_EXPIRED_MESSAGE,
      { variant: "info" },
    );
    expect(window.URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("does not rebuild again when the table already has a new comparison", async () => {
    const compareId = { current: COMPARE_ID };
    mocks.post.mockImplementation(async () => {
      // The table started over while the download was in flight.
      compareId.current = NEW_COMPARE_ID;
      throw {
        statusCode: 404,
        code: COMPARE_NOT_FOUND_CODE,
        result: "This comparison was not found or has expired.",
      };
    });
    const onComparisonExpired = vi.fn();
    const { result, onError } = renderDownloadCompare(
      compareId,
      onComparisonExpired,
    );

    await act(async () => {
      await result.current.mutateAsync();
    });

    expect(onError).not.toHaveBeenCalled();
    expect(onComparisonExpired).not.toHaveBeenCalled();
    // No rebuild started, so no "being rebuilt" message.
    expect(snackbar.enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("reports a dataset that was not found while a comparison was open", async () => {
    // A compared dataset was deleted: a 404 with the generic code.
    const failure = {
      statusCode: 404,
      code: "not_found",
      result: "The specified dataset does not exist or has been deleted.",
    };
    mocks.post.mockRejectedValue(failure);
    const onComparisonExpired = vi.fn();
    const { result, onError } = renderDownloadCompare(
      { current: COMPARE_ID },
      onComparisonExpired,
    );

    await act(async () => {
      await expect(result.current.mutateAsync()).rejects.toBe(failure);
    });

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toBe(failure);
    expect(onComparisonExpired).not.toHaveBeenCalled();
    expect(snackbar.enqueueSnackbar).not.toHaveBeenCalledWith(
      COMPARISON_EXPIRED_MESSAGE,
      expect.anything(),
    );
  });

  it("asks to try again, without rebuilding, when the comparison storage is unavailable", async () => {
    mocks.post.mockRejectedValue({
      statusCode: 503,
      code: COMPARE_STORAGE_UNAVAILABLE_CODE,
      result: "Comparison data is temporarily unavailable. Please try again.",
    });
    const onComparisonExpired = vi.fn();
    const { result, onError } = renderDownloadCompare(
      { current: COMPARE_ID },
      onComparisonExpired,
    );

    await act(async () => {
      await result.current.mutateAsync();
    });

    expect(onComparisonExpired).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(snackbar.enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
      COMPARE_STORAGE_UNAVAILABLE_MESSAGE,
      { variant: "error" },
    );
  });

  it("still reports other download failures", async () => {
    const failure = { statusCode: 500, result: "Something went wrong" };
    mocks.post.mockRejectedValue(failure);
    const onComparisonExpired = vi.fn();
    const { result, onError } = renderDownloadCompare(
      { current: COMPARE_ID },
      onComparisonExpired,
    );

    await act(async () => {
      await expect(result.current.mutateAsync()).rejects.toBe(failure);
    });

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toBe(failure);
    expect(onComparisonExpired).not.toHaveBeenCalled();
    expect(snackbar.enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("reports a 404 when no comparison was sent", async () => {
    const failure = { statusCode: 404, result: "Dataset not found." };
    mocks.post.mockRejectedValue(failure);
    const onComparisonExpired = vi.fn();
    const { result, onError } = renderDownloadCompare(
      { current: null },
      onComparisonExpired,
    );

    await act(async () => {
      await expect(result.current.mutateAsync()).rejects.toBe(failure);
    });

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onComparisonExpired).not.toHaveBeenCalled();
  });
});
