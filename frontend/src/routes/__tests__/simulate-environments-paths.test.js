import { describe, it, expect } from "vitest";
import { paths } from "src/routes/paths";

describe("simulate.environments path constants", () => {
  const env = paths.dashboard.simulate.environments;

  it("declares the root path", () => {
    expect(env.root).toBe("/dashboard/simulate/environments");
  });

  it("declares the templates path", () => {
    expect(env.templates).toBe("/dashboard/simulate/environments/templates");
  });

  it("builds a detail path from an env id", () => {
    expect(env.detail("abc")).toBe("/dashboard/simulate/environments/abc");
  });

  it("no longer declares a standalone build path (build routes to the workspace)", () => {
    expect(env.build).toBeUndefined();
  });

  it("builds a workspace-tab path with the ?tab= query", () => {
    expect(env.workspaceTab("e1", "runs")).toBe(
      "/dashboard/simulate/environments/e1?tab=runs",
    );
  });

  it("builds a nested execution path from env, test and execution ids", () => {
    expect(env.execution("e1", "t1", "x1")).toBe(
      "/dashboard/simulate/environments/e1/runs/t1/x1",
    );
  });
});
