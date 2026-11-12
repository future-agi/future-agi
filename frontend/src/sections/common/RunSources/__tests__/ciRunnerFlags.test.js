// Every flag the CI step prints exists in the runner that step calls (code,
// TypeScript and harness). A CI file that passes a flag the runner does not
// take fails on its first line, so this pins one grammar end to end.
// (dataset integration FE-0: the voice runners and the hosted voice platforms are gone.)
import { describe, expect, it } from "vitest";
import { catalogEntry } from "../catalog";
import { ciKeyModels, ciRunCommand, ciSecrets } from "../snippets/ci";
import { harnessScript } from "../snippets/harness";
import { traceUploadScript } from "../snippets/python";
import { traceUploadScriptTs } from "../snippets/typescript";
import { defaultRecipe, installLine, keyEnvOf } from "../recipe/recipeModel";

const IDS = {
  datasetId: "d",
  experimentId: "e",
  runId: "r",
  runLabel: "#1A",
};
const flagsOf = (cmd) => cmd.match(/(?<=\s)--[a-z-]+/g) ?? [];

describe("CI step flags exist in the runner it calls", () => {
  it.each([
    ["langgraph", (e) => traceUploadScript({ entry: e, ids: IDS })],
    ["mastra", (e) => traceUploadScriptTs({ entry: e, ids: IDS })],
    [
      "claude_code",
      (e) => harnessScript({ entry: e, ids: IDS, recipe: defaultRecipe(e) }),
    ],
  ])("%s", (id, scriptOf) => {
    const entry = catalogEntry(id);
    const cmd = ciRunCommand({
      entry,
      ci: { threshold: 0.7 },
      limits: { tries: 2, concurrency: 3 },
    });
    const script = scriptOf(entry);
    const flags = flagsOf(cmd);
    expect(flags.length).toBeGreaterThan(2);
    flags.forEach((flag) => expect(script).toContain(`"${flag}"`));
  });

  it("a recipe's own model wins over the default for its key", () => {
    const e = catalogEntry("claude_code");
    const recipe = { ...defaultRecipe(e), model: "claude-haiku-4-5" };
    expect(ciKeyModels({ entry: e, recipe })).toEqual(["claude-haiku-4-5"]);
  });
});
