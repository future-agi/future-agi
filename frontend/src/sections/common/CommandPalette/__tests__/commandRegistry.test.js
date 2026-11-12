import { beforeEach, describe, expect, it } from "vitest";
import {
  commandScore,
  pushRecent,
  rankCommands,
  readRecent,
  registeredCommands,
} from "../commandRegistry";
import { datasetIdOfPath, rowNumberOf } from "../providers";
import {
  GLOSSARY,
  GLOSSARY_KEYS,
  searchGlossary,
} from "src/sections/common/ux/glossary";

const CMDS = [
  { id: "view.options", label: "View options…", keywords: ["display"] },
  { id: "help", label: "Keyboard shortcuts", keywords: ["keys"] },
  {
    id: "run.2d",
    label: "#2D Claude Code × claude-sonnet-4-5",
    keywords: ["2D"],
  },
  { id: "run.2b", label: "#2B Support Agent v3", keywords: ["2B"] },
];

describe("palette ranking", () => {
  it("matches every query word; prefixes first", () => {
    expect(rankCommands(CMDS, "view").map((c) => c.id)).toEqual([
      "view.options",
    ]);
    expect(rankCommands(CMDS, "2D").map((c) => c.id)).toEqual(["run.2d"]);
    expect(rankCommands(CMDS, "zzz")).toEqual([]);
    expect(commandScore(CMDS[1], "")).toBe(1);
  });
  it("hides commands whose when() is false", () => {
    const sources = {
      a: [
        { id: "x", label: "X", when: () => false },
        { id: "y", label: "Y" },
      ],
    };
    expect(registeredCommands(sources).map((c) => c.id)).toEqual(["y"]);
  });
});

describe("recents", () => {
  beforeEach(() => window.localStorage.clear());
  it("keeps the last 8, newest first, per user", () => {
    for (let i = 0; i < 10; i += 1)
      pushRecent("u1", { id: `c${i}`, label: `C${i}` });
    pushRecent("u1", { id: "c5", label: "C5" });
    const r = readRecent("u1");
    expect(r).toHaveLength(8);
    expect(r[0].id).toBe("c5");
    expect(readRecent("u2")).toEqual([]);
  });
});

describe("providers helpers", () => {
  it("parses rows and datasets", () => {
    expect(rowNumberOf("T26")).toBe(26);
    expect(rowNumberOf("row 12")).toBe(12);
    expect(rowNumberOf("refund")).toBeNull();
    expect(
      datasetIdOfPath(
        "/dashboard/develop/d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06/compare/matrix",
      ),
    ).toBe("d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06");
    expect(datasetIdOfPath("/dashboard/develop/experiment/x")).toBeNull();
  });
});

describe("glossary", () => {
  it("has every frozen key with short copy", () => {
    [
      "successRate",
      "passAt1",
      "passHatK",
      "reliable",
      "flaky",
      "trial",
      "run",
      "experiment",
      "baseline",
      "delta",
      "ci95",
      "withinNoise",
      "regressed",
      "improved",
      "judge",
      "check",
      "harness",
      "costPerTask",
      "totalCost",
      "timePerTask",
      "firstWrongStep",
      "firstDifference",
      "milestone",
      "differ",
      "carried",
      "preview",
    ].forEach((k) => expect(GLOSSARY[k]?.short, k).toBeTruthy());
    expect(GLOSSARY.passAt1.short).toBe(
      "Share of attempts that succeeded. 65.6% = 59 of 90 attempts.",
    );
    expect(GLOSSARY_KEYS.length).toBeGreaterThanOrEqual(26);
  });
  it("answers `What does pass^3 mean?`", () => {
    expect(searchGlossary("What does pass^3 mean?")[0].key).toBe("passHatK");
    expect(searchGlossary("flaky")[0].key).toBe("flaky");
  });
});
