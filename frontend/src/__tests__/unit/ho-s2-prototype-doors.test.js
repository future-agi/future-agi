// HO S2: the Leaderboards and Audit log doors read only prototype paths, so
// their nav entries and routes exist only in prototype builds. The checks
// read the sources: the gate is a static env expression the build folds, so
// the page chunks are not emitted (the R4Lab precedent, tests F7).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";

const read = (file) =>
  readFileSync(resolve(process.cwd(), "src", file), "utf8");
const nav = read("layouts/dashboard/config-navigation.jsx");
const routes = read("routes/sections/dashboard.jsx");
const STATIC_GATE =
  /const PROTOTYPE_BUILD =\s*import\.meta\.env\.VITE_ENVIRONMENT === "local" &&\s*import\.meta\.env\.VITE_PROTOTYPE_MODE === "true";/;

describe("prototype-only doors", () => {
  it("the nav and the routes hold the one static prototype expression", () => {
    expect(nav).toMatch(STATIC_GATE);
    expect(routes).toMatch(STATIC_GATE);
  });

  it("the Leaderboards nav entry sits inside the static gate", () => {
    expect(nav).toMatch(
      /\.\.\.\(PROTOTYPE_BUILD\s*\?\s*\[\s*\{\s*title: "Leaderboards",/,
    );
    expect(nav.match(/title: "Leaderboards"/g)).toHaveLength(1);
  });

  it("the Audit log entry is static-or-test (the m6 nav test renders it)", () => {
    expect(nav).toMatch(
      /\.\.\.\(PROTOTYPE_BUILD \|\| import\.meta\.env\.MODE === "test"\s*\?\s*\[\s*\{\s*title: "Audit log",/,
    );
    expect(nav.match(/title: "Audit log"/g)).toHaveLength(1);
  });

  it("both pages load only inside the static gate and their routes only when loaded", () => {
    expect(routes).toMatch(
      /const Leaderboards = PROTOTYPE_BUILD\s*\?\s*lazyWithRetry\(\(\) => import\("src\/pages\/dashboard\/Develop\/Leaderboards"\)\)\s*:\s*null;/,
    );
    expect(routes).toMatch(
      /const WorkspaceAuditLog = PROTOTYPE_BUILD\s*\?\s*lazyWithRetry\(\s*\(\) =>\s*import\(\s*"src\/pages\/dashboard\/settings\/WorkspaceSettings\/WorkspaceAuditLog"\s*,?\s*\),?\s*\)\s*:\s*null;/,
    );
    expect(routes).toMatch(
      /\.\.\.\(Leaderboards\s*\?\s*\[\s*\{ path: "leaderboards", element: <Leaderboards \/> \}/,
    );
    expect(routes).toMatch(
      /\.\.\.\(WorkspaceAuditLog\s*\?\s*\[\s*\{\s*path: "audit-log",/,
    );
    expect(
      routes.match(/import\("src\/pages\/dashboard\/Develop\/Leaderboards"\)/g),
    ).toHaveLength(1);
  });
});
