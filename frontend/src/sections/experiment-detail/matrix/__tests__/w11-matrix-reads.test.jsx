// With a second run, the Matrix read every run in view. The results
// endpoint lines up only runs that share one dataset version, trial plan
// and evaluators and refused the mix (409 "Runs require
// the same immutable version, trial plan and evaluator bindings; mixed
// comparison is not supported."): the tab read "Couldn't load the matrix."
// and the app toasted. The Matrix's reads never toast now (the tab says a
// refusal itself), and they wait until the runs to read are known.
import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

const { get, enqueueSnackbar } = vi.hoisted(() => ({
  get: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: {
    develop: {
      datasetRuns: {
        results: (id) => `/model-hub/datasets/${id}/results/`,
        analysis: (id) => `/model-hub/datasets/${id}/results/analysis/`,
        list: (id) => `/model-hub/datasets/${id}/runs/`,
      },
    },
  },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
}));
// R1 is held in a launch build; the matrix and analysis reads are real.
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunConfigs: () => ({ data: undefined, held: true }),
}));

import useMatrixData from "../useMatrixData";
import { handleError } from "src/utils/queryErrorHandler";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const MIXED = {
  statusCode: 409,
  result:
    "Runs require the same immutable version, trial plan and evaluator bindings; mixed comparison is not supported.",
};

let client;
function Wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Wrapper.propTypes = { children: PropTypes.node };

beforeEach(() => {
  get.mockReset();
  enqueueSnackbar.mockReset();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
    queryCache: new QueryCache({ onError: handleError }),
  });
});

describe("the Matrix reads never toast", () => {
  it("leaves a refused matrix read to the tab (plain words, no toast)", async () => {
    get.mockRejectedValue(MIXED);
    const { result } = renderHook(
      () => useMatrixData({ datasetId: "ds-1", runIds: [A, B], baselineId: B }),
      { wrapper: Wrapper },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    await new Promise((r) => setTimeout(r, 30));
    expect(result.current.error).toEqual(MIXED);
    // Before: the app toast said MIXED_RUNS_WORDS on top of the tab's error.
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("sends no read until the runs to read are known", async () => {
    get.mockRejectedValue(MIXED);
    renderHook(
      () =>
        useMatrixData({
          datasetId: "ds-1",
          runIds: [A, B],
          baselineId: B,
          enabled: false,
        }),
      { wrapper: Wrapper },
    );
    await new Promise((r) => setTimeout(r, 30));
    expect(get).not.toHaveBeenCalled();
  });
});
