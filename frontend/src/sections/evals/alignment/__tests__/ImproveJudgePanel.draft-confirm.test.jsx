import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import ImproveJudgePanel from "../ImproveJudgePanel";
import { costClause, judgeCallsCost } from "../judgeCost";

// Draft candidates spends judge calls and one writer call on the server
// (eval_improve.improve_judge: a baseline plus up to three candidates, each
// scored on every labelled row), so it is confirmed with its cost first.
const mocks = vi.hoisted(() => ({ improve: vi.fn() }));
vi.mock("../alignmentApi", () => ({
  editableCopyOf: vi.fn(),
  useCustomPrices: () => ({ customPrices: null, refresh: vi.fn() }),
  useImprove: () => ({ mutateAsync: mocks.improve, isPending: false }),
  useRefreshAgreement: () => vi.fn(),
}));
vi.mock("src/sections/evals/hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({ data: { versions: [] } }),
}));
vi.mock("notistack", () => ({ useSnackbar: () => ({ enqueueSnackbar: vi.fn() }) }));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: { develop: { eval: { createEvalVersion: () => "/v/" } } },
}));

const JUDGE = "turing_large";
const report = { n: 6, judge_model: JUDGE, source_key: "binding:label", labelled_rows: [] };

const renderPanel = () =>
  render(
    <ImproveJudgePanel
      templateId="10000000-0000-4000-8000-000000000001"
      templateName="Correctness"
      mode="improve"
      report={report}
    />,
  );

const fillSplit = () => {
  fireEvent.change(screen.getByLabelText("Draft share %"), { target: { value: "50" } });
  fireEvent.change(screen.getByLabelText("Seed"), { target: { value: "seed-1" } });
};

describe("ImproveJudgePanel Draft candidates confirm", () => {
  beforeEach(() => {
    mocks.improve.mockReset();
    mocks.improve.mockResolvedValue({ candidates: [], split: { draft_rows: [], check_rows: [] } });
  });
  afterEach(() => vi.clearAllMocks());

  it("shows the judge calls and cost before drafting, and Cancel makes no request", async () => {
    renderPanel();
    fillSplit();
    fireEvent.click(screen.getByTestId("w7-draft"));

    const cost = judgeCallsCost(6, [JUDGE, JUDGE, JUDGE, JUDGE], null);
    expect(cost.calls).toBe(24);
    expect(screen.getByTestId("w7-draft-confirm")).toHaveTextContent(
      `Draft up to 3 candidates and score 6 labelled rows 4 times · up to 24 judge calls · ${costClause(cost)} · plus one writer call`,
    );
    expect(mocks.improve).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    // The dialog keeps its content while its close transition runs.
    await waitFor(() => expect(screen.queryByTestId("w7-draft-confirm")).not.toBeInTheDocument());
    expect(mocks.improve).not.toHaveBeenCalled();
  });

  it("drafts only after the confirm, once, with the split it shows", async () => {
    renderPanel();
    fillSplit();
    fireEvent.click(screen.getByTestId("w7-draft"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("w7-draft-go"));
    });
    expect(mocks.improve).toHaveBeenCalledTimes(1);
    expect(mocks.improve).toHaveBeenCalledWith({
      source_key: "binding:label",
      source_template_id: "10000000-0000-4000-8000-000000000001",
      draft_share: 50,
      seed: "seed-1",
    });
  });
});
