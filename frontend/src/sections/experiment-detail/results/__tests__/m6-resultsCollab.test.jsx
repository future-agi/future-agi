// M6 (W4 §9 integration on the results page): the selection bar's Send to
// review… / Assign to… / Comment…, and the Review tab's comments keyed by
// (row, run) on the dataset the results belong to.
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { COMMENT_ONE_ROW, resultsCollabActions } from "../resultsCollabActions";

const calls = [];
vi.mock("src/api/develop/row-collab", () => ({
  useRowComments: (datasetId, rowId, opts) => {
    calls.push({ datasetId, rowId, opts });
    return { data: { threads: [{}, {}] } };
  },
}));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: null }),
}));
vi.mock("src/sections/common/Collab/CommentThread", () => ({
  default: (props) => (
    <div data-testid="threads">{`${props.datasetId}|${props.rowId}|${props.runId}`}</div>
  ),
}));
import RunCommentsSection from "src/sections/common/Collab/RunCommentsSection";

const ids = (items) => items.map((i) => i.id);
const byId = (items, id) => items.find((i) => i.id === id);
const run = { run_id: "run-4a", label: "#4A" };
const noop = () => {};

describe("resultsCollabActions", () => {
  it("an editor with one row: Send to QA, Assign to and Comment", () => {
    const items = resultsCollabActions({
      n: 1,
      readOnly: false,
      access: { can: { edit: true }, reason: () => null },
      qaRun: run,
      onSendToQa: noop,
      onAssign: noop,
      onComment: noop,
    });
    expect(ids(items)).toEqual(["sendToQa", "assign", "comment"]);
    expect(items.map((i) => i.label)).toEqual([
      "Send to QA…",
      "Assign to…",
      "Comment…",
    ]);
    expect(items.every((i) => !i.disabled)).toBe(true);
  });

  it("several rows: Comment takes one row at a time", () => {
    const items = resultsCollabActions({
      n: 3,
      readOnly: false,
      access: null,
      qaRun: run,
      onComment: noop,
    });
    expect(byId(items, "comment")).toMatchObject({
      disabled: true,
      reason: COMMENT_ONE_ROW,
    });
    expect(byId(items, "assign").disabled).toBe(false);
  });

  it("without edit access, Assign and Send to QA carry the server's reason", () => {
    const why = "You have View access to this dataset. You need edit access.";
    const items = resultsCollabActions({
      n: 1,
      readOnly: false,
      access: { can: { edit: false }, reason: () => why },
      qaRun: run,
      onComment: noop,
    });
    expect(byId(items, "assign")).toMatchObject({
      disabled: true,
      reason: why,
    });
    expect(byId(items, "sendToQa")).toMatchObject({
      disabled: true,
      reason: why,
    });
    // Viewers may comment.
    expect(byId(items, "comment").disabled).toBe(false);
    const viewer = resultsCollabActions({ n: 1, readOnly: true, qaRun: run });
    expect(byId(viewer, "assign").reason).toBe("Editors can assign rows");
    expect(byId(viewer, "sendToQa").reason).toBe("Editors can start a review");
    expect(byId(viewer, "comment")).toBeUndefined();
  });
});

describe("RunCommentsSection on the results page", () => {
  it("reads the (row, run) thread of the dataset the drawer passes", () => {
    render(
      <MemoryRouter>
        <RunCommentsSection
          datasetId="ds-gs"
          rowId="row-12"
          runId="run-4a"
          runLabel="#4A"
        />
      </MemoryRouter>,
    );
    expect(calls.at(-1)).toMatchObject({
      datasetId: "ds-gs",
      rowId: "row-12",
      opts: { runId: "run-4a" },
    });
    expect(screen.getByTestId("threads").textContent).toBe(
      "ds-gs|row-12|run-4a",
    );
    expect(screen.getByTestId("run-comments").textContent).toContain(
      "Comments on #4A's answer (2)",
    );
  });
});
