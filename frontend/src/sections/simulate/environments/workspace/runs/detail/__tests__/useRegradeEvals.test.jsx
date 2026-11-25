import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useRunNewEvals } from "src/api/simulate-environments/runEvals";
import { enqueueSnackbar } from "notistack";
import { useRegradeEvals } from "../useRegradeEvals";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/simulate-environments/runEvals", () => ({
  useRunNewEvals: vi.fn(),
}));

let mutate;
beforeEach(() => {
  enqueueSnackbar.mockReset();
  mutate = vi.fn();
  useRunNewEvals.mockReturnValue({ mutate, isPending: false });
});

const setup = () =>
  renderHook(() => useRegradeEvals({ envId: "env-1", executionId: "ex1" }));

describe("useRegradeEvals", () => {
  it("grades the given evals on this run", () => {
    const { result } = setup();
    result.current.regrade([{ id: "c1" }, { id: "c2" }]);
    expect(mutate).toHaveBeenCalledWith(
      { id: "env-1", executionId: "ex1", evalConfigIds: ["c1", "c2"] },
      expect.any(Object),
    );
  });

  it("says grading started once it is queued", () => {
    const onSuccess = vi.fn();
    const { result } = setup();
    result.current.regrade([{ id: "c1" }], { onSuccess });
    mutate.mock.calls[0][1].onSuccess({ call_execution_count: 4 });

    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Grading 1 evaluation. This run updates when grading finishes.",
      { variant: "success" },
    );
    expect(onSuccess).toHaveBeenCalledWith();
  });

  it("shows the server's reason on a refusal and reports nothing", () => {
    const onSuccess = vi.fn();
    const { result } = setup();
    result.current.regrade([{ id: "c1" }], { onSuccess });
    mutate.mock.calls[0][1].onError({
      detail: "This run is still finishing. Try again in a moment.",
    });

    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "This run is still finishing. Try again in a moment.",
      { variant: "error" },
    );
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("falls back to its own sentence when grading couldn't be queued", () => {
    const onSuccess = vi.fn();
    const { result } = setup();
    result.current.regrade([{ id: "c1" }], { onSuccess });
    mutate.mock.calls[0][1].onError({ statusCode: 503 });

    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Grading may not have started. Try again.",
      { variant: "error" },
    );
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it("passes the mutation's pending state through", () => {
    useRunNewEvals.mockReturnValue({ mutate, isPending: true });
    expect(setup().result.current.isPending).toBe(true);
  });
});
