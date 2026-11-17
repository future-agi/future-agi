import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as notFound from "../DatasetNotFound";

const { statusOfError, useDatasetMissing } = notFound;

const h = vi.hoisted(() => ({ get: null }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    default: { ...real.default, get: (...a) => h.get(...a) },
  };
});

describe("statusOfError", () => {
  it("reads the app's rejected shape and a raw axios error", () => {
    expect(
      statusOfError({
        status: false,
        result: "Dataset not found",
        statusCode: 404,
      }),
    ).toBe(404);
    expect(statusOfError({ response: { status: 404 } })).toBe(404);
    expect(statusOfError({ status: 500 })).toBe(500);
    expect(
      statusOfError({ status: false, message: "Network Error" }),
    ).toBeNull();
    expect(statusOfError(null)).toBeNull();
  });
});

// PF-3 (PUX-5): what src/utils/axios.js rejects with when dev's
// get-dataset-table refuses a missing or another org's dataset
// (bad_request(DATASET_NOT_FOUND), tfc/utils/error_codes.py).
const MESSAGE = "The specified dataset does not exist or has been deleted.";
const devRefusal = () => ({
  status: false,
  type: "validation_error",
  code: "invalid",
  detail: MESSAGE,
  message: MESSAGE,
  error: MESSAGE,
  result: MESSAGE,
  statusCode: 400,
  transportCode: "ERR_BAD_REQUEST",
});

const mount = (datasetId, list) => {
  const client = new QueryClient();
  const wrapper = ({ children }) =>
    createElement(QueryClientProvider, { client }, children);
  return renderHook(
    () => useDatasetMissing(datasetId, { list, listLoading: false }),
    { wrapper },
  );
};
const settle = () => new Promise((r) => setTimeout(r, 60));

describe("useDatasetMissing on the real backend (PF-3)", () => {
  afterEach(() => {
    h.get = null;
  });

  it("dev's 400 DATASET_NOT_FOUND refusal means the dataset is missing", async () => {
    h.get = vi.fn(() => Promise.reject(devRefusal()));
    const { result } = mount("ds-of-org-a", [{ datasetId: "ds-own" }]);
    await waitFor(() => expect(result.current).toBe(true), { timeout: 8000 });
    // A deep link loaded under load is asked again before giving up.
    expect(h.get.mock.calls.length).toBeGreaterThan(1);
  });

  it("a server error (500) is not 'not found'", async () => {
    h.get = vi.fn(() =>
      Promise.reject({ status: false, message: "boom", statusCode: 500 }),
    );
    const { result } = mount("ds-x", [{ datasetId: "ds-own" }]);
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(1));
    await settle();
    expect(result.current).toBe(false);
    expect(h.get).toHaveBeenCalledTimes(1);
  });

  it("another 400 (not the not-found refusal) is not 'not found'", async () => {
    h.get = vi.fn(() =>
      Promise.reject({
        ...devRefusal(),
        detail: "Invalid filter.",
        message: "Invalid filter.",
        error: "Invalid filter.",
        result: "Invalid filter.",
      }),
    );
    const { result } = mount("ds-x", [{ datasetId: "ds-own" }]);
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(1));
    await settle();
    expect(result.current).toBe(false);
  });

  it("an id the list has sends no request and is not missing", async () => {
    h.get = vi.fn(() => Promise.reject(devRefusal()));
    const { result } = mount("ds-own", [{ datasetId: "ds-own" }]);
    await settle();
    expect(h.get).not.toHaveBeenCalled();
    expect(result.current).toBe(false);
  });

  it("isDatasetGoneError (pure): 404, the pinned 400 message, or a not-found code", () => {
    expect(typeof notFound.isDatasetGoneError).toBe("function");
    const gone = notFound.isDatasetGoneError;
    expect(notFound.DATASET_NOT_FOUND_MESSAGE).toBe(MESSAGE);
    expect(gone({ statusCode: 404 })).toBe(true);
    expect(gone(devRefusal())).toBe(true);
    expect(
      gone({ statusCode: 400, code: "dataset_not_found", detail: "x" }),
    ).toBe(true);
    expect(gone({ statusCode: 400, detail: "Invalid filter." })).toBe(false);
    expect(gone({ statusCode: 500, detail: MESSAGE })).toBe(false);
    expect(gone(null)).toBe(false);
  });
});
