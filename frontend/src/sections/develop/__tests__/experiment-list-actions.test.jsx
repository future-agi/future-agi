import { afterEach, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
const route = vi.hoisted(() => vi.fn());
vi.mock("react-router", () => ({ useNavigate: () => route }));
import { useExperimentListActions } from "../experimentListActions";
afterEach(() => {
  setLaunchOverrideForTest(null);
  route.mockClear();
});
it("adds only flag-admitted experiment navigation to filtered row actions", () => {
  const remove = vi.fn();
  setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  const { result } = renderHook(() => useExperimentListActions({ remove }));
  expect(result.current.remove).toBe(remove);
  result.current.newExperiment({ id: "dataset-a" });
  expect(route).toHaveBeenLastCalledWith(
    "/dashboard/develop/dataset-a?tab=data&new_experiment=1",
  );
  result.current.openExperiments({ id: "dataset-a" });
  expect(route).toHaveBeenLastCalledWith(
    "/dashboard/develop/dataset-a?tab=experiments",
  );
});
it("retains exactly the two classic doors with the basic flag off, not NEW doors", () => {
  setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
  const { result } = renderHook(() => useExperimentListActions({}));
  expect(result.current).toEqual({
    openExperiments: expect.any(Function),
    newExperiment: expect.any(Function),
  });
  result.current.openExperiments({ id: "dataset-a" });
  result.current.newExperiment({ id: "dataset-a" });
  expect(route.mock.calls).toEqual([
    ["/dashboard/develop/dataset-a?tab=experiments"],
    ["/dashboard/develop/dataset-a?tab=data&new_experiment=1"],
  ]);
  for (const id of ["exp.new", "exp.new.kindMenu", "exp.new.record"]) {
    expect(isSurfaceShown(id), id).toBe(false);
  }
});
it("preserves a caller's existing navigation callbacks", () => {
  setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  const actions = { newExperiment: vi.fn(), openExperiments: vi.fn() };
  const { result } = renderHook(() => useExperimentListActions(actions));
  expect(result.current).toEqual(actions);
});
