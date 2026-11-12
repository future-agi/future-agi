import { describe, expect, it, vi } from "vitest";
import { waitingOf, terminalRunOf } from "../waitingModel";

// These checks pin the command the released SDK runner prints; while it is
// unreleased no screen prints it (sdk-runner-not-released.test.jsx).
vi.mock("../sdkRunner", () => ({ SDK_RUNNER_RELEASED: true }));

const run = {
  run_id: "created-run-id", label: "#1", trials: 3,
  config: { kind: "code", framework: { id: "langchain" }, execution: { mode: "local" },
    limits: { tries: 3, concurrency: 2, timeout_s: 90 }, row_labels: ["T2", "T5"] },
};

describe("post-create local command", () => {
  it("uses the SDK composer with the persisted run id and retained options", () => {
    const wait = waitingOf(run, { entryId: "langchain", mode: "local", config: run.config });
    expect(wait.command).toMatch(/^futureagi run /);
    expect(wait.command).toContain("--run created-run-id");
    expect(wait.command).toContain("--rows T2,T5");
    expect(wait.command).toContain("--trials 3");
    expect(wait.command).toContain("--concurrency 2");
    expect(wait.command).toContain("--timeout-s 90");
    expect(wait.command).not.toMatch(/run_and_upload|simulate/);
    expect(wait.unavailable).toBeNull();
    expect(terminalRunOf(run).command).toBe(wait.command);
  });
  it("does not offer a local runner for uncreated or nonlocal runs", () => {
    expect(terminalRunOf({ ...run, run_id: null })).toBeNull();
    for (const mode of ["ci", "sandbox", "remote", "upload"]) {
      expect(terminalRunOf({ ...run, config: { ...run.config, execution: { mode } } })).toBeNull();
    }
  });
});
