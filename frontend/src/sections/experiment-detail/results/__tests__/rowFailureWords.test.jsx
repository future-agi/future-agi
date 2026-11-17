// A failed row's words reach a person in three places: the results grid's
// cell (the cell's `error`, set by the server only to a sentence a code
// chose), the row drawer's output card (the try's error message) and the
// run's status chip (`First error`). Green before the server sends them, by
// design: no component changes, and these pin what a person then sees.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import OutputCell from "../cells/OutputCell";
import OutputCard from "../drawer/OutputCard";
import RunStatusChip from "src/sections/common/Runs/RunStatusChip";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
// The tooltip's words render inline, so the chip's hover lines can be read.
vi.mock("src/components/tooltip", () => ({
  default: ({ title, children }) => (
    <div>
      <div data-testid="tip">{title}</div>
      {children}
    </div>
  ),
}));
vi.mock("src/sections/common/Trace/TraceChip", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/Review/CorrectedOutputChip", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Runs/RunChip", () => ({
  default: () => null,
}));
vi.mock("../drawer/CellHistory", () => ({ default: () => null }));
vi.mock("../RunSourceBlock", async (original) => ({
  ...(await original()),
  useRunWithConfig: () => ({ full: null }),
}));

const UNCATALOGUED =
  "Retired Model A isn't a model this workspace can run. Choose another model for this prompt.";
const RUN_FAILED = "Run failed on this row";
const PROVIDER_ERROR = "The model provider returned an error.";
const RUN = {
  run_id: "run-prompt",
  label: "#1A",
  target_type: "prompt",
  target: {},
};

const failedCell = (code, message, cellError = message) => ({
  status: "error",
  output: null,
  error: cellError,
  trials: 1,
  trials_detail: [
    {
      trial_number: 1,
      status: "error",
      output: null,
      error: { type: "Failure", code, message },
    },
  ],
});

const gridText = (result) =>
  render(
    <OutputCell
      data={{ example_id: "ex-1", results: { "run-prompt": result } }}
      runId="run-prompt"
      context={{ runsById: { "run-prompt": RUN } }}
    />,
  ).container.textContent;

const drawerText = (result) =>
  render(<OutputCard run={RUN} result={result} trial={1} row={{}} />).container
    .textContent;

describe("a failed row's words", () => {
  it("the grid cell shows the sentence its code chose", () => {
    expect(
      gridText(failedCell("model_not_in_catalog", UNCATALOGUED)),
    ).toContain(`Error · ${UNCATALOGUED}`);
    expect(gridText(failedCell("provider_failed", PROVIDER_ERROR))).toContain(
      `Error · ${PROVIDER_ERROR}`,
    );
  });

  it("a grid cell with no coded sentence keeps its fallback", () => {
    const text = gridText(
      failedCell(undefined, "graph text the server keeps", null),
    );
    expect(text).toContain(`Error · ${RUN_FAILED}`);
    expect(text).not.toContain("graph text the server keeps");
  });

  it("the row drawer's output card shows the try's sentence", () => {
    expect(drawerText(failedCell("model_not_in_catalog", UNCATALOGUED))).toContain(
      `Error · ${UNCATALOGUED}`,
    );
    const general = drawerText(failedCell("row_failed", RUN_FAILED));
    expect(general).toContain(`Error · ${RUN_FAILED}`);
    expect(general).not.toContain("Native");
    expect(general).not.toContain("[object Object]");
  });

  it("the run's status chip shows its First error", () => {
    const { container } = render(
      <RunStatusChip
        run={{
          run_id: "run-prompt",
          label: "#1A",
          status: "Failed",
          progress: { done: 2, total: 2, errors: 2 },
          first_error: UNCATALOGUED,
        }}
      />,
    );
    expect(
      container.querySelector('[data-testid="tip"]').textContent,
    ).toContain(`First error: ${UNCATALOGUED}`);
  });
});
