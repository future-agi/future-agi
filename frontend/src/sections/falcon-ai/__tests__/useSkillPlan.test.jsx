import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "src/utils/test-utils";
import useFalconStore from "../store/useFalconStore";
import useSkillPlan from "../hooks/useSkillPlan";

const getSkill = vi.fn();

vi.mock("../hooks/useFalconAPI", () => ({
  getSkill: (...args) => getSkill(...args),
}));

const EVAL_BUILD = {
  id: "skill-1",
  slug: "eval-build",
  name: "Eval Build",
};

const TRAJECTORIES = [
  {
    user: "Go ahead and build the four evals you recommended.",
    steps: [
      { tool: "get_project" },
      { tool: "get_eval_template_by_name" },
      { tool: "get_eval_task_logs" },
    ],
  },
];

function seed(userContent, skills = [EVAL_BUILD]) {
  useFalconStore.setState({
    skills,
    skillPlans: {},
    messages: [
      { id: "u1", role: "user", content: userContent },
      { id: "a1", role: "assistant", content: "" },
    ],
  });
}

beforeEach(() => {
  getSkill.mockReset();
  useFalconStore.getState().resetAll();
  useFalconStore.setState({ skillPlans: {} });
});

describe("useSkillPlan", () => {
  it("reads the declared flow of the skill the turn ran", async () => {
    getSkill.mockResolvedValue({
      status: true,
      result: { ...EVAL_BUILD, example_trajectories: TRAJECTORIES },
    });
    seed("/eval-build build the four evals");

    const { result } = renderHook(() => useSkillPlan("a1", []));

    await waitFor(() =>
      expect(result.current).toEqual([
        "get_project",
        "get_eval_template_by_name",
        "get_eval_task_logs",
      ]),
    );
    expect(getSkill).toHaveBeenCalledWith("skill-1");
  });

  it("fetches a skill's flow once and keeps it", async () => {
    getSkill.mockResolvedValue({
      result: { example_trajectories: TRAJECTORIES },
    });
    seed("/eval-build go");

    const first = renderHook(() => useSkillPlan("a1", []));
    await waitFor(() => expect(first.result.current).toHaveLength(3));

    const second = renderHook(() => useSkillPlan("a1", []));
    await waitFor(() => expect(second.result.current).toHaveLength(3));
    expect(getSkill).toHaveBeenCalledTimes(1);
  });

  it("has no flow for a turn that ran no skill", () => {
    seed("build me four evals");
    const { result } = renderHook(() => useSkillPlan("a1", []));
    expect(result.current).toEqual([]);
    expect(getSkill).not.toHaveBeenCalled();
  });

  it("has no flow for a slug that is not a skill", () => {
    seed("/not-a-skill go");
    const { result } = renderHook(() => useSkillPlan("a1", []));
    expect(result.current).toEqual([]);
    expect(getSkill).not.toHaveBeenCalled();
  });

  it("has no flow when the skill declares none", async () => {
    getSkill.mockResolvedValue({ result: { example_trajectories: [] } });
    seed("/eval-build go");
    const { result } = renderHook(() => useSkillPlan("a1", []));
    await waitFor(() => expect(getSkill).toHaveBeenCalled());
    expect(result.current).toEqual([]);
  });

  it("does not fetch when the skill already carries its flow", () => {
    seed("/eval-build go", [
      { ...EVAL_BUILD, example_trajectories: TRAJECTORIES },
    ]);
    const { result } = renderHook(() => useSkillPlan("a1", []));
    expect(result.current).toHaveLength(3);
    expect(getSkill).not.toHaveBeenCalled();
  });
});
