import React from "react";
import PropTypes from "prop-types";
import { act, renderHook } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import {
  COMPARISON_EXPIRED_MESSAGE,
  COMPARE_STORAGE_UNAVAILABLE_MESSAGE,
  isCompareNotFound,
  useDownloadCompare,
} from "../dataset-compare";

const snackbar = vi.hoisted(() => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/snackbar", () => snackbar);
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));

const BASE_ID = "00000000-0000-4000-8000-000000000001";
const PEER_ID = "00000000-0000-4000-8000-000000000002";
const OLD_ID = "00000000-0000-4000-8000-000000000003";
const NEW_ID = "00000000-0000-4000-8000-000000000004";
const DOWNLOAD_URL = `/model-hub/datasets/${BASE_ID}/compare-datasets/download/`;
const EXPIRED_MESSAGE =
  "This comparison was not found or has expired. Open the comparison again.";

// Exact build_error_envelope(string, status_code=404, code=compare_not_found)
// shape used by _compare_not_found_response. There is NO statusCode on the
// wire: this test runs the real shared Axios interceptor that supplies it.
const envelope = (type, code, message) => ({
  status: false,
  type,
  code,
  detail: message,
  message,
  error: message,
  result: message,
});
const expiredEnvelope = () =>
  envelope("not_found", "compare_not_found", EXPIRED_MESSAGE);
const originalAdapter = axios.defaults.adapter;
let adapter;

function rejectWith(status, body) {
  adapter.mockImplementation((config) =>
    Promise.reject({
      config,
      code: "ERR_BAD_RESPONSE",
      response: { status, data: body, config },
    }),
  );
}

function setup(compareId = { current: OLD_ID }) {
  const onError = vi.fn();
  const onComparisonExpired = vi.fn(() => {
    compareId.current = null;
  });
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError }),
    defaultOptions: { mutations: { retry: false } },
  });
  function Wrapper({ children }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  const { result } = renderHook(
    () =>
      useDownloadCompare({
        compareId,
        selectedDatasetsValues: [BASE_ID, PEER_ID],
        baseColumn: "input_key",
        onComparisonExpired,
      }),
    { wrapper: Wrapper },
  );
  return { result, compareId, onComparisonExpired, onError };
}

beforeEach(() => {
  vi.clearAllMocks();
  adapter = vi.fn();
  axios.defaults.adapter = adapter;
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = vi.fn(() => "blob:synthetic-comparison");
      static revokeObjectURL = vi.fn();
    },
  );
});

afterEach(() => {
  axios.defaults.adapter = originalAdapter;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("comparison download backend error envelope", () => {
  it("rebuilds once for typed 404 with valid selection and no client rows, then downloads using the new ID", async () => {
    rejectWith(404, expiredEnvelope());
    const { result, compareId, onComparisonExpired, onError } = setup();
    await act(async () => {
      await result.current.mutateAsync();
    });

    expect(adapter).toHaveBeenCalledTimes(1);
    const request = adapter.mock.calls[0][0];
    expect(request.url).toBe(DOWNLOAD_URL);
    expect(JSON.parse(request.data)).toEqual({
      dataset_ids: [PEER_ID],
      base_column_name: "input_key",
      compare_id: OLD_ID,
    });
    expect(onComparisonExpired).toHaveBeenCalledTimes(1);
    expect(compareId.current).toBeNull();
    expect(onError).not.toHaveBeenCalled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(snackbar.enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
      COMPARISON_EXPIRED_MESSAGE,
      { variant: "info" },
    );

    // Rebuilding the table is the caller's job; the hook never retries the
    // download automatically or sends incomplete client-row fallback data.
    compareId.current = NEW_ID;
    adapter.mockImplementation(async (config) => ({
      config,
      status: 200,
      data: "input_key,answer\nsynthetic,value\n",
      headers: { "content-type": "text/csv" },
    }));
    await act(async () => {
      await result.current.mutateAsync();
    });
    expect(adapter).toHaveBeenCalledTimes(2);
    expect(JSON.parse(adapter.mock.lastCall[0].data).compare_id).toBe(NEW_ID);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(onComparisonExpired).toHaveBeenCalledTimes(1);
  });

  it.each([
    [
      404,
      "not_found",
      "not_found",
      "The specified dataset does not exist or has been deleted.",
    ],
    [403, "permission_error", "permission_denied", "Permission denied."],
    [400, "validation_error", "invalid", "Unable to download dataset."],
    [503, "service_unavailable", "service_unavailable", "Service unavailable."],
    [403, "permission_error", "compare_not_found", "Permission denied."],
  ])(
    "does not rebuild or retry HTTP %i with %s/%s",
    async (status, type, code, message) => {
      const body = envelope(type, code, message);
      rejectWith(status, body);
      const { result, compareId, onComparisonExpired, onError } = setup();
      await act(async () => {
        await expect(result.current.mutateAsync()).rejects.toMatchObject({
          ...body,
          statusCode: status,
          transportCode: "ERR_BAD_RESPONSE",
        });
      });
      expect(adapter).toHaveBeenCalledTimes(1);
      expect(onComparisonExpired).not.toHaveBeenCalled();
      expect(compareId.current).toBe(OLD_ID);
      expect(onError).toHaveBeenCalledTimes(1);
      expect(isCompareNotFound(onError.mock.calls[0][0])).toBe(false);
      expect(URL.createObjectURL).not.toHaveBeenCalled();
      expect(snackbar.enqueueSnackbar).not.toHaveBeenCalled();
    },
  );

  it("keeps the ID on the actual typed storage-503 envelope", async () => {
    rejectWith(
      503,
      envelope(
        "service_unavailable",
        "compare_storage_unavailable",
        COMPARE_STORAGE_UNAVAILABLE_MESSAGE,
      ),
    );
    const { result, compareId, onComparisonExpired, onError } = setup();
    await act(async () => {
      await result.current.mutateAsync();
    });
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(compareId.current).toBe(OLD_ID);
    expect(onComparisonExpired).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(snackbar.enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
      COMPARE_STORAGE_UNAVAILABLE_MESSAGE,
      { variant: "error" },
    );
  });

  it("ignores an expired download that completes after a successful table ID rollover", async () => {
    const compareId = { current: OLD_ID };
    adapter.mockImplementation((config) => {
      compareId.current = NEW_ID;
      return Promise.reject({
        config,
        response: { status: 404, data: expiredEnvelope(), config },
      });
    });
    const { result, onComparisonExpired, onError } = setup(compareId);
    await act(async () => {
      await result.current.mutateAsync();
    });
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(compareId.current).toBe(NEW_ID);
    expect(onComparisonExpired).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(snackbar.enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("reports typed 404 without restarting when no comparison ID was sent", async () => {
    rejectWith(404, expiredEnvelope());
    const { result, onComparisonExpired, onError } = setup({ current: null });
    await act(async () => {
      await expect(result.current.mutateAsync()).rejects.toMatchObject({
        statusCode: 404,
        code: "compare_not_found",
      });
    });
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(onComparisonExpired).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
  });
});
