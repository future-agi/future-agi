import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import CorrectionsBlock from "../CorrectionsBlock";
import { versionNote } from "../correctionModel";

// W4 C1: the review summary's Corrections block — accept / dismiss one by
// one, save the accepted ones as one N2 version naming every reviewer, add
// them to the pending changes, or link one the dataset already holds.
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
const notify = vi.fn();
vi.mock("src/sections/common/ux/toast", () => ({
  default: (...a) => notify(...a),
}));

let corrections = [];
let canApply = true;
const patch = vi.fn();
vi.mock("src/api/develop/review", () => ({
  useReviewCorrections: () => ({
    data: { corrections, can_apply: canApply },
  }),
  patchCorrection: (...a) => patch(...a),
  refreshCorrections: vi.fn(),
}));
const save = vi.fn();
vi.mock("src/api/develop/dataset-versions", () => ({
  useDatasetVersions: () => ({
    data: [{ id: "ver-6", number: 6, is_latest: true }],
  }),
  latestOf: (v) => v?.find((x) => x.is_latest) ?? null,
  useSaveDatasetVersion: () => ({ mutateAsync: (...a) => save(...a) }),
}));
const addOp = vi.fn();
vi.mock("src/sections/develop-detail/DataTab/Versions/changeSetStore", () => ({
  useChangeSetStore: (sel) => sel({ addOp }),
}));

const fix = (id, row, reviewer, extra = {}) => ({
  id,
  dataset_id: "d1",
  row_id: `r-${row}`,
  row_label: `Row ${row}`,
  column_id: "c-exp",
  column_name: "expected_reply",
  kind: "expected",
  before: "old answer",
  after: `new answer ${row}`,
  current: "old answer",
  in_dataset: false,
  stale: false,
  status: "suggested",
  reviewer: { id: reviewer, name: reviewer, email: null, outside: false },
  source: { queue_id: "q1", queue_name: "Quality · #4A", item_id: `i-${row}` },
  updated_at: `2026-10-22T10:0${row}:00Z`,
  ...extra,
});

const renderBlock = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CorrectionsBlock datasetId="d1" queueId="q1" />
    </QueryClientProvider>,
  );

describe("CorrectionsBlock", () => {
  beforeEach(() => {
    canApply = true;
    patch.mockReset().mockResolvedValue({});
    save.mockReset().mockResolvedValue({ version: { number: 7 } });
    addOp.mockReset();
    notify.mockReset();
    corrections = [fix("k1", 33, "Meera Iyer"), fix("k2", 40, "Asha Rao")];
  });

  it("saves the accepted corrections as one version naming the reviewers, then marks each applied", async () => {
    renderBlock();
    expect(screen.getByText("Corrections (2)")).toBeTruthy();
    expect(
      screen.getByText("Row 33 · expected_reply · by Meera Iyer"),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Save 2 corrections as v7" }),
    );
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(2));
    expect(save).toHaveBeenCalledWith({
      base_version_id: "ver-6",
      description: versionNote(corrections),
      operations: [
        expect.objectContaining({
          op: "update_cell",
          row_id: "r-33",
          column_id: "c-exp",
          value: "new answer 33",
        }),
        expect.objectContaining({
          op: "update_cell",
          row_id: "r-40",
          value: "new answer 40",
        }),
      ],
    });
    expect(versionNote(corrections)).toMatch(/Meera Iyer/);
    expect(versionNote(corrections)).toMatch(/Asha Rao/);
    expect(patch).toHaveBeenCalledWith("d1", "k1", {
      status: "applied",
      version_number: 7,
    });
  });

  it("an unaccepted correction stays out of the version", async () => {
    renderBlock();
    fireEvent.click(screen.getAllByRole("button", { name: "Accepted" })[1]);
    fireEvent.click(
      screen.getByRole("button", { name: "Save 1 correction as v7" }),
    );
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0].operations).toHaveLength(1);
    expect(save.mock.calls[0][0].operations[0].row_id).toBe("r-33");
  });

  it("adds the accepted corrections to the pending changes with their source", () => {
    renderBlock();
    fireEvent.click(
      screen.getByRole("button", { name: "Add to pending changes" }),
    );
    expect(addOp).toHaveBeenCalledTimes(2);
    expect(addOp).toHaveBeenCalledWith("d1", {
      op: "update_cell",
      row_id: "r-33",
      column_id: "c-exp",
      value: "new answer 33",
      before: "old answer",
      // M6 (W4 §9 Versions integration): the source also names the reviewer
      // and the review; every op carries the default note.
      source: expect.objectContaining({
        kind: "review_correction",
        correction_id: "k1",
        reviewer: expect.objectContaining({ name: "Meera Iyer" }),
      }),
      description_hint: expect.stringMatching(/^2 corrections/),
    });
    expect(save).not.toHaveBeenCalled();
  });

  it("a correction the dataset already holds is linked to the latest version, not saved again", async () => {
    corrections = [
      fix("k1", 33, "Meera Iyer", {
        in_dataset: true,
        current: "new answer 33",
      }),
    ];
    renderBlock();
    expect(screen.getByText("Already in the dataset")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Accept/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Save / })).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Mark as saved in v6" }),
    );
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith("d1", "k1", {
        status: "applied",
        version_number: 6,
      }),
    );
    expect(save).not.toHaveBeenCalled();
  });

  it("without edit access the list is read-only", () => {
    canApply = false;
    renderBlock();
    expect(
      screen.getByText(
        "Only people who can edit this dataset save corrections.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Accept/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Add to pending changes" }),
    ).toBeNull();
  });
});
