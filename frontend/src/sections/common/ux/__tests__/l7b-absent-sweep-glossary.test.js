import { describe, expect, it } from "vitest";
import {
  GLOSSARY,
  GLOSSARY_KEYS,
  glossaryEntry,
  searchGlossary,
} from "src/sections/common/ux/glossary";

// options-truth L7b absent-sweep: the glossary (Cmd+K, the ? sheet) never
// describes a path that is absent (Q12: Your code, Agent harness, Voice
// agent, linked traces, where-it-runs modes, runners, gateway capture).
const REMOVED =
  /your (own )?code|harness runs|voice agent|this machine|your terminal|teammates|gateway URL/i;
const text = (e) =>
  [e.label, e.short, e.long ?? "", ...(e.aliases ?? [])].join(" ");

describe("l7b absent-sweep glossary", () => {
  it("lists no absent entry in search, for any query", () => {
    const queries = [
      "",
      "where it runs",
      "sandbox",
      "teammates",
      "gateway",
      "declared",
      "harness",
      "baton",
      "this machine",
      "capture",
    ];
    queries.forEach((q) => {
      searchGlossary(q).forEach(({ key, entry }) => {
        expect(entry.absent, `${q} -> ${key}`).toBeFalsy();
        expect(text(entry), `${q} -> ${key}`).not.toMatch(REMOVED);
      });
    });
    expect(searchGlossary("baton")).toEqual([]);
    expect(searchGlossary("teammates")).toEqual([]);
    expect(searchGlossary("gateway").map((r) => r.key)).not.toContain(
      "gatewayCapture",
    );
  });

  it("every non-absent entry is free of removed-path copy", () => {
    GLOSSARY_KEYS.filter((k) => !GLOSSARY[k].absent).forEach((k) =>
      expect(text(GLOSSARY[k]), k).not.toMatch(REMOVED),
    );
  });

  it("keeps the hover-card entries seeded runs still point at", () => {
    ["harness", "executionMode", "captureMode", "declaredParam"].forEach(
      (k) => {
        expect(glossaryEntry(k)?.short, k).toBeTruthy();
        expect(glossaryEntry(k).absent, k).toBe(true);
      },
    );
    expect(glossaryEntry("executionMode").label).toBe("Where it ran");
    expect(glossaryEntry("captureMode").short).not.toMatch(/gateway/i);
    expect(glossaryEntry("baton")).toBeNull();
    expect(glossaryEntry("gatewayCapture")).toBeNull();
    expect(glossaryEntry("allowTeammates")).toBeNull();
  });

  it("names the run kinds that exist and the Simulated tag without a terminal", () => {
    // Only the ways an experiment runs today: A2A agents and MCP tools are
    // greyed in the picker, so the definition does not call them runnable.
    expect(GLOSSARY.run.short).toContain(
      "(a prompt, an agent graph or an HTTP endpoint)",
    );
    expect(GLOSSARY.run.short).not.toMatch(/A2A|MCP/);
    expect(GLOSSARY.run.short).toContain("rows or tasks");
    expect(GLOSSARY.preview.short).toMatch(
      /^Simulated: the tag next to a stand-in button/,
    );
    expect(GLOSSARY.preview.short).not.toMatch(/exactly as a real one would/);
  });
});
