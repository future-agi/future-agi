import { describe, expect, it } from "vitest";
import { runNameFor } from "src/sections/common/RunSources/sourceModel";
import { autoName } from "../RunExperiment";

const card = (entryName, kind = "harness") => ({
  targetType: kind,
  name: entryName,
  runName: entryName,
  r4: { kind, entryName },
});

describe("IT-R2 G1 P4 · names you chose stay", () => {
  it("suggests dataset · runs · date", () => {
    const now = Date.UTC(2026, 9, 18, 12);
    expect(
      autoName(
        [
          card("Claude Code"),
          card("Codex CLI"),
          card("Custom TypeScript", "code"),
        ],
        { datasetName: "Support ops tasks · sandbox", now },
      ),
    ).toBe(
      "Support ops · Claude Code vs Codex CLI vs Custom TypeScript · Oct 18",
    );
    expect(autoName([], { now })).toBeNull();
  });

  it("a run picked from a saved agent is named by it", () => {
    expect(
      runNameFor({ entryId: "claude_code", savedName: "Repo fixer" }),
    ).toBe("Repo fixer · Claude Code");
    // dataset integration FE-0: the voice entries are gone, so a voice id names nothing
    expect(runNameFor({ entryId: "livekit" })).toBe("");
  });
});
