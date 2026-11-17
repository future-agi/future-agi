// Where the deployment does not offer row assignments (collabOffer.js), the
// results selection bar keeps Assign to… listed and enabled; choosing it
// shows the one sentence as an info notice instead of the assign dialog,
// and nothing is sent to the assignments routes. Comment… stays listed and
// opens the row's answer, whose comments section says the same for
// comments (run-comments-not-offered.test.jsx).
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import axios from "src/utils/axios";
import { setCollabOfferForTest } from "src/sections/common/Collab/collabOffer";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";

const h = vi.hoisted(() => ({ say: vi.fn(), iterable: false }));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: h.say,
}));
vi.mock("src/sections/common/ux/oneview/ActionBar", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ actions }) =>
      createElement(
        "div",
        { "data-testid": "actions" },
        actions.map((a) =>
          createElement(
            "button",
            {
              key: a.id,
              type: "button",
              disabled: Boolean(a.disabled),
              onClick: () => a.run?.(),
            },
            a.label,
          ),
        ),
      ),
  };
});
vi.mock("src/sections/common/Collab/AssignDialog", async () => {
  const { createElement } = await import("react");
  return {
    default: () => createElement("div", { "data-testid": "assign-dialog" }),
  };
});
vi.mock("src/sections/develop-detail/Review/StartReviewDialog", () => ({
  default: () => null,
}));
vi.mock("../AddResultsToDatasetDialog", () => ({
  default: ({ open, exampleIds }) =>
    open ? (
      <output data-testid="copy-dialog">{exampleIds.join(",")}</output>
    ) : null,
}));
vi.mock("src/sections/common/export/ExportDialog", () => ({
  ResultsExportDialog: () => null,
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { edit: true, download: true },
    reason: () => null,
    isLoading: false,
  }),
}));
vi.mock("src/api/develop/run-results", () => ({
  useRerunResultCells: () => ({ mutate: vi.fn() }),
}));
vi.mock("src/sections/common/Runs/useRescore", () => ({
  useRescore: () => ({ request: vi.fn(), dialog: null }),
}));
vi.mock("src/sections/common/Runs/rescoreCopyModel", () => ({
  scoreWithChoices: () => [],
}));
vi.mock("src/sections/common/Runs/runKind", () => ({
  canRerunHere: () => false,
}));
vi.mock("../../iterate/iterateModel", () => ({ canIterate: () => h.iterable }));
vi.mock("src/sections/common/Analysis/analysisLinks", () => ({
  iterateHref: () => "",
}));
vi.mock("src/sections/common/RunSources/rerunBus", () => ({
  openRerunWithChanges: vi.fn(),
}));
vi.mock("src/sections/common/Selection/askFalconAboutRows", () => ({
  askFalconAboutRows: vi.fn(),
}));
vi.mock("src/sections/common/Selection/useSelectAllMatching", () => ({
  useSelectAllMatching: ({ selectedIds }) => ({ ids: selectedIds }),
}));
vi.mock("src/sections/common/Selection/SelectionCount", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/export/exportSelection", () => ({
  resultsSelectionView: () => ({}),
}));

const { default: SelectionBar } = await import("../SelectionBar");

const SENTENCE = "Assigning rows is not part of this release.";
const run = { run_id: "run-4a", label: "#4A" };

const mount = (props = {}) =>
  render(
    <MemoryRouter>
      <SelectionBar
        datasetId="ds-1"
        selectedRows={[{ example_id: "ex-1" }]}
        runs={[run]}
        baselineId="run-4a"
        focusedRunId="run-4a"
        readOnly={false}
        onClear={() => {}}
        onComment={() => {}}
        {...props}
      />
    </MemoryRouter>,
  );

const assignmentsCalls = (spy) =>
  spy.mock.calls.filter(([url]) => String(url).includes("/assignments/"));

let get;
let post;
beforeEach(() => {
  h.iterable = false;
  h.say.mockClear();
  setCollabOfferForTest({ all: false });
  get = vi.spyOn(axios, "get").mockResolvedValue({ data: { result: {} } });
  post = vi.spyOn(axios, "post").mockResolvedValue({ data: { result: {} } });
});
afterEach(() => {
  setLaunchOverrideForTest(null);
  cleanup();
  setCollabOfferForTest(null);
  vi.restoreAllMocks();
});

describe("results selection bar where row assignments are not offered", () => {
  it("lists Assign to… and Comment…, both enabled", () => {
    mount();
    const assign = screen.getByRole("button", { name: "Assign to…" });
    const comment = screen.getByRole("button", { name: "Comment…" });
    expect(assign.disabled).toBe(false);
    expect(comment.disabled).toBe(false);
  });

  it("Assign to… shows the sentence as an info notice and opens no dialog", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Assign to…" }));
    expect(screen.queryByTestId("assign-dialog")).toBeNull();
    expect(h.say).toHaveBeenCalledTimes(1);
    expect(h.say).toHaveBeenCalledWith(SENTENCE, { variant: "info" });
    expect(assignmentsCalls(get)).toEqual([]);
    expect(assignmentsCalls(post)).toEqual([]);
  });

  it("Comment… still opens the row's answer", () => {
    const onComment = vi.fn();
    mount({ onComment });
    fireEvent.click(screen.getByRole("button", { name: "Comment…" }));
    expect(onComment).toHaveBeenCalledWith({ example_id: "ex-1" }, "run-4a");
  });

  it("where assigning is offered, Assign to… opens the dialog", () => {
    setCollabOfferForTest({
      all: false,
      madeReal: ["dataset.collab.assignments"],
    });
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Assign to…" }));
    expect(screen.getByTestId("assign-dialog")).toBeTruthy();
    expect(h.say).not.toHaveBeenCalledWith(SENTENCE, expect.anything());
  });
});

describe("accepted Results selection actions", () => {
  it("opens the selected rows copy dialog without an Iterate action", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      showUnreleased: false,
    });
    h.iterable = true;
    expect(isSurfaceShown("exp.results.addToDataset")).toBe(true);
    expect(isSurfaceShown("exp.results.iterateDock")).toBe(false);
    mount();
    const add = screen.getByRole("button", { name: "Add to dataset…" });
    expect(add.disabled).toBe(false);
    expect(
      screen.queryByRole("button", { name: "Test a fix on these rows" }),
    ).toBeNull();
    expect(screen.queryByTestId("copy-dialog")).toBeNull();
    fireEvent.click(add);
    expect(screen.getByTestId("copy-dialog").textContent).toBe("ex-1");
  });
});
