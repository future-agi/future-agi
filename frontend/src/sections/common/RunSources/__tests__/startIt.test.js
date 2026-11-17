// IT-R1 G1 P4: Start it is composed only from each run's own snippet.
import { describe, expect, it } from "vitest";
import { snippetFor } from "../snippets";
import { runStartCommand, startItPlan } from "../snippets/startIt";
import { recipeSummary, defaultRecipe } from "../recipe/recipeModel";
import { catalogEntry } from "../catalog";
import {
  ciRunsOf,
  selfStartLine,
  selfStartRunsOf,
  terminalRunsOf,
} from "src/sections/develop-detail/Experiment/StartItBlock";

const RUNS = [
  {
    label: "#4A",
    name: "Claude Code",
    kind: "harness",
    entryId: "claude_code",
  },
  { label: "#4B", name: "Codex CLI", kind: "harness", entryId: "codex_cli" },
  {
    label: "#4C",
    name: "support_agent · Custom TypeScript",
    kind: "code",
    entryId: "custom_typescript",
    lang: "typescript",
  },
];

describe("Start it (IT-R1 G1 P4)", () => {
  it("each run's command is its drawer command, in its own folder", () => {
    const c = runStartCommand(RUNS[2]);
    const drawer = snippetFor({
      kind: "code",
      entryId: "custom_typescript",
      lang: "typescript",
    });
    expect(c.command).toBe(
      drawer.runCommand.replace(drawer.fileName, `run-4C/${drawer.fileName}`),
    );
    expect(runStartCommand(RUNS[0]).command).toBe(
      "python run-4A/run_harness_and_upload.py --workdir .",
    );
  });

  it("one script starts them with & and waits; CI runs the same file", () => {
    const plan = startItPlan({ ordinal: 4, runs: RUNS });
    expect(plan.shellName).toBe("run-experiment-4.sh");
    const lines = plan.shell.split("\n");
    expect(lines.filter((l) => / & {2}# #4[ABC]/.test(l))).toHaveLength(3);
    expect(lines).toContain("wait");
    expect(plan.ci).toContain("      - run: bash run-experiment-4.sh");
    expect(plan.ci).toContain(
      "ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}",
    );
    expect(plan.ci).toContain("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}");
    expect(plan.ci).toContain("actions/setup-node@v4");
  });

  it("a harness recipe reads as one line", () => {
    const entry = catalogEntry("claude_code");
    const r = defaultRecipe(entry, {
      mcp: { name: "acme-support-sandbox", tools: 12 },
    });
    expect(recipeSummary(r)).toBe(
      "claude-sonnet-4-5 · MCP acme-support (12 tools) · permissions: edit files · $1.00 per try",
    );
  });
});

// dataset integration FE-0: the hosted voice platforms are out; a voice card starts
// nothing (no Terminal, CI or self-start line).
describe("Start it has no voice path", () => {
  const card = (entryId, mode) => ({ r4: { kind: "voice", entryId, mode } });
  const runs = [{ configIndex: 0, letter: "A" }];
  it("no Terminal, CI or self-start run for a voice card", () => {
    ["vapi", "livekit"].forEach((id) => {
      const cards = [card(id, "ci")];
      expect(ciRunsOf(cards, runs)).toHaveLength(0);
      expect(selfStartRunsOf(cards, runs)).toHaveLength(0);
    });
  });
});
