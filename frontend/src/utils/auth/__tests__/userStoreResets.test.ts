import { afterEach, describe, expect, it, vi } from "vitest";

import { resetAllUserStores } from "../clearStores";
import { registerUserStoreReset, resetUserStore } from "../userStoreResets";
import logger from "src/utils/logger";
import { useWorkflowRunStore } from "src/sections/agent-playground/store/workflow-run-store";

const noOp = () => {};

afterEach(() => {
  registerUserStoreReset("agent-playground", noOp);
  registerUserStoreReset("graph-builder", noOp);
  vi.restoreAllMocks();
});

describe("custom user store resets", () => {
  it("does nothing until a store registers", () => {
    expect(resetUserStore("agent-playground")).toBeUndefined();
    expect(resetUserStore("graph-builder")).toBeUndefined();
  });

  it("replaces a registration instead of accumulating callbacks", () => {
    const previous = vi.fn();
    const current = vi.fn();
    registerUserStoreReset("agent-playground", previous);
    registerUserStoreReset("agent-playground", current);

    expect(resetUserStore("agent-playground")).toBeUndefined();
    expect(previous).not.toHaveBeenCalled();
    expect(current).toHaveBeenCalledOnce();
    resetUserStore("agent-playground");
    expect(current).toHaveBeenCalledTimes(2);
  });

  it("keeps custom resets in their original order after the generic reset", () => {
    const calls: string[] = [];
    useWorkflowRunStore.setState({ isRunning: true });
    const workflowReset = vi.spyOn(
      useWorkflowRunStore.getInitialState(),
      "reset",
    );
    registerUserStoreReset("agent-playground", () => {
      expect(useWorkflowRunStore.getState().isRunning).toBe(false);
      expect(workflowReset).not.toHaveBeenCalled();
      calls.push("agent-playground");
    });
    registerUserStoreReset("graph-builder", () => {
      expect(workflowReset).toHaveBeenCalledOnce();
      calls.push("graph-builder");
    });

    expect(resetAllUserStores()).toBeUndefined();
    expect(calls).toEqual(["agent-playground", "graph-builder"]);
  });

  it("continues synchronously if a registered reset fails", () => {
    const error = new Error("reset failed");
    const log = vi.spyOn(logger, "error").mockImplementation(noOp);
    const graphReset = vi.fn();
    registerUserStoreReset("agent-playground", () => {
      throw error;
    });
    registerUserStoreReset("graph-builder", graphReset);

    expect(resetAllUserStores()).toBeUndefined();
    expect(log).toHaveBeenCalledWith(
      "Failed to reset agent-playground store on logout",
      error,
    );
    expect(graphReset).toHaveBeenCalledOnce();
  });
});
