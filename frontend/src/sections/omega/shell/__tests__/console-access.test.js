// CP P10: the host's mirror of the session exchange's role rule. The cases
// are pinned in fixtures/omega-delegation-roles.v1.json, and the same fixture
// is checked against the rule's own source (futureagi/accounts/omega_session.py
// delegation_role), so the two cannot drift without this test failing.
import { readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";

import { CONSOLE_ACCESS, consoleAccessFor } from "../omega-access";
import cases from "./fixtures/omega-delegation-roles.v1.json";

const ROLE_TO_ACCESS = {
  owner: CONSOLE_ACCESS.ALLOWED,
  admin: CONSOLE_ACCESS.ALLOWED,
  member: CONSOLE_ACCESS.ALLOWED,
  refused: CONSOLE_ACCESS.VIEWER,
};

// The rule as Django runs it, read from its source: each `if` line's levels
// and the role it returns, then the refusal for everything else.
function djangoRule() {
  const file = path.resolve(
    process.cwd(),
    "../futureagi/accounts/omega_session.py",
  );
  const source = readFileSync(file, "utf8");
  const body =
    /def delegation_role\(org_level, workspace_level\):([\s\S]*?)\n\S/.exec(
      source,
    )?.[1];
  if (!body) throw new Error("delegation_role not found");
  const rules = [];
  const pattern =
    /if org_level == (\d+)(?: and workspace_level in \(([\d, ]+)\))?:\s*\n\s*return "(\w+)"/g;
  let match;
  while ((match = pattern.exec(body))) {
    rules.push({
      org: Number(match[1]),
      ws: match[2] ? match[2].split(",").map((v) => Number(v.trim())) : null,
      role: match[3],
    });
  }
  if (!/raise ValueError/.test(body)) throw new Error("no refusal branch");
  return (org, ws) =>
    rules.find((rule) => rule.org === org && (!rule.ws || rule.ws.includes(ws)))
      ?.role ?? "refused";
}

describe("Omega Console access from Future AGI's levels", () => {
  it("agrees with the session exchange's own rule on every pinned case", () => {
    const rule = djangoRule();
    expect(cases.cases.length).toBeGreaterThan(10);
    for (const { org, ws, role } of cases.cases) {
      expect(rule(org, ws)).toBe(role);
      expect(consoleAccessFor(org, ws)).toBe(ROLE_TO_ACCESS[role]);
    }
  });

  it("never reads levels it does not have as a refusal", () => {
    expect(consoleAccessFor(null, null)).toBe(CONSOLE_ACCESS.UNKNOWN);
    expect(consoleAccessFor(undefined, 3)).toBe(CONSOLE_ACCESS.UNKNOWN);
    expect(consoleAccessFor("", 3)).toBe(CONSOLE_ACCESS.UNKNOWN);
    // An organization member whose workspace grant has not loaded yet.
    expect(consoleAccessFor(3, null)).toBe(CONSOLE_ACCESS.UNKNOWN);
    // Levels kept as strings in sessionStorage still read as levels.
    expect(consoleAccessFor("15", null)).toBe(CONSOLE_ACCESS.ALLOWED);
    expect(consoleAccessFor("3", "1")).toBe(CONSOLE_ACCESS.VIEWER);
    expect(consoleAccessFor("abc", 3)).toBe(CONSOLE_ACCESS.UNKNOWN);
  });
});
