import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import {
  PROTOTYPE_ABSENT_FALLBACK,
  isAbsentInPrototype,
} from "../prototypeAbsentRoutes";

// L7b absent-sweep (nav:other-products, settings:pages, url:dev-pages,
// url:observe-voice, state:misc).
describe("l7b absent-sweep: pages absent from a prototype build", () => {
  it("lands the other products and unmocked settings on the Datasets list", () => {
    expect(PROTOTYPE_ABSENT_FALLBACK).toBe("/dashboard/develop");
    [
      "/dashboard/error-feed",
      "/dashboard/error-feed/abc",
      "/dashboard/tasks",
      "/dashboard/users",
      "/dashboard/users/u-1",
      "/dashboard/alerts",
      "/dashboard/dashboards/d1/widget/w1",
      "/dashboard/gateway",
      "/dashboard/gateway/providers",
      "/dashboard/get-started",
      "/dashboard/simulate/environments",
      "/dashboard/simulate/scenarios/s1",
      "/dashboard/simulate/personas",
      "/dashboard/simulate/test",
      "/dashboard/settings/user-management",
      "/dashboard/settings/usage-summary",
      "/dashboard/settings/billing",
      "/dashboard/settings/pricing",
      "/dashboard/settings/mcp-server",
      "/dashboard/settings/org-settings",
      "/dashboard/settings/ee-licenses",
      "/dashboard/settings/integrations",
      "/dashboard/settings/integrations/c1",
      // L7c g1: no Falcon AI connector can be added in the prototype.
      "/dashboard/settings/falcon-ai-connectors",
      "/dashboard/settings/workspace/w1/members",
      "/dashboard/settings/workspace/w1/integrations",
    ].forEach((p) => expect(isAbsentInPrototype(p), p).toBe(true));
  });

  it("keeps every Datasets + Experiments page and the mocked settings", () => {
    [
      "/dashboard/develop",
      "/dashboard/develop/d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e01",
      "/dashboard/develop/leaderboards",
      "/dashboard/observe",
      "/dashboard/observe/p1/llm-tracing",
      "/dashboard/observe/p1/users",
      "/dashboard/annotations/queues",
      "/dashboard/knowledge",
      "/dashboard/evaluations",
      "/dashboard/falcon-ai",
      "/dashboard/simulate/agent-definitions",
      "/dashboard/settings/profile-settings",
      "/dashboard/settings/ai-providers",
      "/dashboard/settings/workspace",
      "/dashboard/settings/workspace/w1/general",
      "/dashboard/settings/workspace/w1/audit-log",
      "/dashboard/settings/workspace/w1/ai-providers",
      "/dashboard/tasks-compare",
      "/dashboard/usersettings",
    ].forEach((p) => expect(isAbsentInPrototype(p), p).toBe(false));
  });

  it("wires the gate behind the static prototype flag only, and drops dev pages", () => {
    const routes = readFileSync(
      resolve(process.cwd(), "src/routes/sections/dashboard.jsx"),
      "utf8",
    );
    expect(routes).toMatch(
      /PROTOTYPE_BUILD && isAbsentInPrototype\(location\.pathname\) \?\s*\(\s*<Navigate to=\{PROTOTYPE_ABSENT_FALLBACK\} replace \/>/,
    );
    expect(routes).toMatch(
      /const R4Lab =\s*PROTOTYPE_BUILD && import\.meta\.env\.DEV\s*\?/,
    );
    expect(routes).not.toMatch(/loremipsum/);
    // No rendered "coming soon" page (commented-out code aside).
    expect(
      routes
        .split("\n")
        .filter((l) => !l.trim().startsWith("//"))
        .join("\n"),
    ).not.toMatch(/coming soon/i);
    expect(routes).toMatch(
      /path: "voice",\s*element: <Navigate to="\.\.\/llm-tracing" replace \/>/,
    );
    const layout = readFileSync(
      resolve(process.cwd(), "src/layouts/dashboard/index.jsx"),
      "utf8",
    );
    expect(layout).not.toContain("OneViewGallery");
    expect(layout).not.toContain("oneview_gallery");
    expect(layout).not.toContain("showGallery");
  });
});
