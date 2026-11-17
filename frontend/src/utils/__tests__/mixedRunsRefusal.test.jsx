// Comparing runs whose dataset version, trial plan or evaluators differ
// (inside one experiment or across experiments): the results read refuses
// with "Runs require the same immutable version, trial plan and evaluator
// bindings; mixed comparison is not supported." (409), and the compare page
// showed that sentence. The app now says it in plain words (toast and page),
// with the first run's experiment one click away; other errors are unchanged.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { handleError } from "src/utils/queryErrorHandler";
import {
  MIXED_RUNS_TOAST_KEY,
  MIXED_RUNS_WORDS,
  experimentDataHref,
  isMixedRunsRefusal,
  refusalText,
} from "src/utils/mixedRunsRefusal";
import MixedRunsNotice from "src/sections/experiment-detail/results/MixedRunsNotice";

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
}));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));

const SENTENCE =
  "Runs require the same immutable version, trial plan and evaluator bindings; mixed comparison is not supported.";
const REFUSED = { statusCode: 409, result: SENTENCE };

describe("the mixed-runs refusal", () => {
  beforeEach(() => enqueueSnackbar.mockReset());

  it("is recognised in every shape a read rejects with", () => {
    expect(isMixedRunsRefusal(REFUSED)).toBe(true);
    expect(
      isMixedRunsRefusal({
        response: { status: 409, data: { result: SENTENCE } },
      }),
    ).toBe(true);
    expect(isMixedRunsRefusal(new Error(SENTENCE))).toBe(true);
    expect(isMixedRunsRefusal(SENTENCE)).toBe(true);
    expect(refusalText(REFUSED)).toBe(SENTENCE);
  });

  it("leaves every other refusal alone", () => {
    for (const error of [
      {
        statusCode: 409,
        result: "Immutable provenance unavailable; use classic results.",
      },
      { statusCode: 404, result: "Run not found on this dataset" },
      {
        statusCode: 400,
        result: "filters: Unsupported filter type in this checkpoint.",
      },
      null,
      {},
    ])
      expect(isMixedRunsRefusal(error)).toBe(false);
  });

  it("toasts the plain words, once per page, never the raw sentence", () => {
    handleError(REFUSED, { options: { meta: {} } });
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    const [text, opts] = enqueueSnackbar.mock.calls[0];
    expect(text).toBe(MIXED_RUNS_WORDS);
    expect(text).not.toContain("immutable");
    expect(opts).toMatchObject({
      variant: "error",
      key: MIXED_RUNS_TOAST_KEY,
      preventDuplicate: true,
    });
  });

  it("keeps other errors' toasts as they were", () => {
    handleError(
      { statusCode: 404, result: "Run not found on this dataset" },
      { options: { meta: {} } },
    );
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar.mock.calls[0][0]).toBe(
      "Run not found on this dataset",
    );
  });

  it("stays quiet for a read that handles its own error", () => {
    handleError(REFUSED, { options: { meta: { errorHandled: true } } });
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });
});

describe("MixedRunsNotice", () => {
  it("shows the plain words and opens the first run's experiment", () => {
    render(
      <MixedRunsNotice
        datasetId="ds 1"
        experimentId="exp-1"
        experimentName="Support agent v2"
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain(MIXED_RUNS_WORDS);
    const open = screen.getByTestId("mixed-runs-open-experiment");
    expect(open.textContent).toBe("Open Support agent v2");
    expect(open.getAttribute("href")).toBe(
      "/dashboard/develop/experiment/exp-1/data?datasetId=ds%201",
    );
    expect(document.body.textContent).not.toContain("immutable");
  });

  it("names the experiment generically and links nowhere without its id", () => {
    const { unmount } = render(<MixedRunsNotice experimentId="exp-2" />);
    expect(screen.getByTestId("mixed-runs-open-experiment").textContent).toBe(
      "Open the first run's experiment",
    );
    unmount();
    render(<MixedRunsNotice datasetId="ds" />);
    expect(screen.queryByTestId("mixed-runs-open-experiment")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain(MIXED_RUNS_WORDS);
    expect(experimentDataHref(null, "e")).toBe(
      "/dashboard/develop/experiment/e/data",
    );
  });

  it("keeps Retry and its R shortcut, which read the rows again", () => {
    const onRetry = vi.fn();
    render(<MixedRunsNotice datasetId="ds" onRetry={onRetry} />);
    screen.getByRole("button", { name: /^Retry/ }).click();
    expect(onRetry).toHaveBeenCalledTimes(1);
    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "r", bubbles: true }),
    );
    expect(onRetry).toHaveBeenCalledTimes(2);
  });

  it("says what differs, true inside one experiment or across experiments", () => {
    expect(MIXED_RUNS_WORDS).toMatch(
      /different dataset versions, trial plans or evaluators/,
    );
    expect(MIXED_RUNS_WORDS).not.toMatch(/different experiments/);
  });
});
