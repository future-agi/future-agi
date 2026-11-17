// HO results R6 review fix: planRescore is the confirm's own plan read (the
// very request with dry_run, merged per experiment, guarded for vN), so the
// Re-score on vN door and the confirm can never disagree.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { planRescore } from "src/sections/common/Runs/useRescore";

const post = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: { post: (...a) => post(...a) },
  endpoints: {
    develop: {
      experiment: {
        reRunExperimentCell: (id) => `/rerun/${id}/`,
        runEvaluation: (id) => `/run-evals/${id}/`,
      },
    },
  },
}));

const runs = [
  { run_id: "a", label: "#1A", experiment_id: "x1" },
  { run_id: "b", label: "#2A", experiment_id: "x2" },
];
const ask = {
  runs,
  evalIdsOf: () => ["u1"],
  evalNames: ["Correctness"],
  datasetVersion: 7,
};
const planOf = (extra) => ({
  data: { result: { plan: { runs: [], judge_calls: 3, ...extra } } },
});

describe("planRescore", () => {
  beforeEach(() => post.mockReset());

  it("sends the request itself with dry_run, one per experiment, carrying the version", async () => {
    post.mockResolvedValue(planOf({ dataset_version: 7 }));
    const plan = await planRescore(ask);
    expect(post.mock.calls.map((c) => c[0])).toEqual([
      "/rerun/x1/",
      "/rerun/x2/",
    ]);
    post.mock.calls.forEach(([, body]) => {
      expect(body.dry_run).toBe(true);
      expect(body.dataset_version).toBe(7);
      expect(body.user_eval_metric_ids).toEqual(["u1"]);
    });
    expect(plan.error).toBeUndefined();
    expect(plan.dataset_version).toBe(7);
  });

  it("refuses when any experiment's plan is not vN's", async () => {
    post
      .mockResolvedValueOnce(planOf({ dataset_version: 7 }))
      .mockResolvedValueOnce(planOf({}));
    const plan = await planRescore(ask);
    expect(plan.error).toBe(true);
    expect(plan.reason).toBe(
      "The server did not plan this re-score on v7, so nothing was sent.",
    );
  });

  it("keeps any other request's plan as it is", async () => {
    post.mockResolvedValue(planOf({}));
    const plan = await planRescore({ ...ask, datasetVersion: undefined });
    expect(plan.error).toBeUndefined();
    expect(plan.judge_calls).toBe(6);
  });
});
