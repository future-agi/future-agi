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
});
