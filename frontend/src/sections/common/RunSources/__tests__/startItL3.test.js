// options-truth L3 G6: I6 (one run command builder), Start it's keys and
// CLI installs, per-run CI files and Start it's whole-experiment file.
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import { catalogEntry } from "../catalog";
import {
  runStartCommand,
  runStartLine,
  startItPlan,
} from "../snippets/startIt";
import {
  ciCommitFiles,
  ciExperimentWorkflow,
  ciSecretCommands,
  ciWorkflow,
  ciWorkflowPath,
} from "../snippets/ci";
import { defaultRecipe, installLine } from "../recipe/recipeModel";

const RUN = "a6000000-0000-4000-8000-00000000004a";

describe("I6 runStartLine", () => {
  it("a code run's line carries every limit, the changed params and the rows", () => {
    const line = runStartLine({
      run: { run_id: RUN, config: { framework: { id: "langgraph" } } },
      kind: "code",
      entry: catalogEntry("langgraph"),
      tries: 3,
      concurrency: 4,
      maxCostUsd: 2.5,
      timeoutS: 90,
      params: { model: "gpt-4.1" },
      rows: ["T2", "T5"],
    });
    expect(line).toBe(
      `python run_and_upload.py --agent agent.py:graph --run ${RUN} --rows T2,T5 --trials 3 --concurrency 4 --max-cost-usd 2.5 --timeout-s 90 --param model=gpt-4.1`,
    );
  });
  it("the run's own agent spec wins; TypeScript runs use npx tsx", () => {
    const line = runStartLine({
      run: {
        run_id: RUN,
        config: {
          framework: { id: "custom_python" },
          code: { entry: "bot/app.py:support" },
        },
      },
      kind: "code",
    });
    expect(line).toContain("--agent bot/app.py:support");
    expect(
      runStartLine({
        run: { run_id: RUN },
        kind: "code",
        entry: catalogEntry("mastra"),
      }),
    ).toMatch(/^npx tsx run_and_upload\.ts /);
  });
  it("a harness line runs the harness runner with the run's limits", () => {
    expect(
      runStartLine({
        run: { run_id: RUN },
        kind: "harness",
        entry: catalogEntry("claude_code"),
        tries: 2,
        timeoutS: 40,
      }),
    ).toBe(
      `python run_harness_and_upload.py --workdir . --run ${RUN} --trials 2 --timeout-s 40`,
    );
  });
  it("called I6-style runStartCommand returns the line; Start it gets the entry", () => {
    const args = {
      run: { run_id: RUN },
      kind: "code",
      entry: catalogEntry("langgraph"),
      tries: 2,
    };
    expect(runStartCommand(args)).toBe(runStartLine(args));
    const entry = runStartCommand({
      label: "#4A",
      kind: "code",
      entryId: "langgraph",
      ids: { runId: RUN, tries: 2 },
    });
    expect(entry.command).toBe(
      runStartLine(args).replace(
        "run_and_upload.py",
        "run-4A/run_and_upload.py",
      ),
    );
    expect(entry.agentFile).toBe("agent.py");
  });
});

describe("Start it keys and CLI installs", () => {
  it("lists every model key and installs each CLI at its pinned version", () => {
    const claude = {
      ...defaultRecipe(catalogEntry("claude_code")),
      version: "2.0.14",
    };
    const plan = startItPlan({
      ordinal: 4,
      runs: [
        {
          label: "#4A",
          name: "LangGraph",
          kind: "code",
          entryId: "langgraph",
          models: ["gpt-4.1-mini"],
          ids: { runId: RUN },
        },
        {
          label: "#4B",
          name: "Claude Code",
          kind: "harness",
          entryId: "claude_code",
          recipe: claude,
          ids: { runId: RUN },
        },
        {
          label: "#4C",
          name: "Gemini CLI",
          kind: "harness",
          entryId: "gemini_cli",
          recipe: {
            ...defaultRecipe(catalogEntry("gemini_cli")),
            version: "0.9.0",
          },
          ids: { runId: RUN },
        },
      ],
    });
    expect(plan.secrets).toEqual([
      "FI_API_KEY",
      "FI_SECRET_KEY",
      "OPENAI_API_KEY",
      "ANTHROPIC_API_KEY",
      "GEMINI_API_KEY",
    ]);
    plan.secrets.forEach((k) =>
      expect(plan.ci).toContain(`${k}: \${{ secrets.${k} }}`),
    );
    expect(plan.ci).toContain(`- run: ${installLine(claude)}`);
    expect(plan.ci).toContain("- run: npm i -g @google/gemini-cli@0.9.0");
    expect(() => parse(plan.ci)).not.toThrow();
  });
});

describe("CI files (S3)", () => {
  it("each run's job has its own file; Start it's file is fi-experiment.yml", () => {
    expect(ciWorkflowPath("#4A")).toBe(
      ".github/workflows/fi-experiment-4a.yml",
    );
    expect(ciWorkflowPath("#12B")).toBe(
      ".github/workflows/fi-experiment-12b.yml",
    );
    const yaml = ciWorkflow({
      entry: catalogEntry("gemini_cli"),
      ids: { runLabel: "#4C", runId: RUN },
      recipe: {
        ...defaultRecipe(catalogEntry("gemini_cli")),
        version: "0.9.0",
      },
    });
    const doc = parse(yaml);
    expect(doc.name).toBe("fi-experiment-4c");
    const runs = doc.jobs.experiment.steps.map((s) => s.run).filter(Boolean);
    expect(runs).toContain(
      "pip install futureagi fi-instrumentation-otel requests",
    );
    expect(runs).toContain("npm i -g @google/gemini-cli@0.9.0");
  });
  it("Start it with several CI runs: one job per run, in its folder, with its id", () => {
    const text = ciExperimentWorkflow({
      ordinal: 4,
      datasetName: "Support ops tasks · sandbox",
      runs: [
        {
          label: "#4A",
          entry: catalogEntry("custom_python"),
          runId: RUN,
          folder: "run-4A",
          models: ["gpt-4.1-mini"],
          limits: { tries: 2, concurrency: 3 },
        },
        {
          label: "#4B",
          entry: catalogEntry("claude_code"),
          runId: "a6000000-0000-4000-8000-00000000004b",
          folder: "run-4B",
          recipe: defaultRecipe(catalogEntry("claude_code")),
        },
      ],
    });
    const doc = parse(text);
    expect(doc.name).toBe("fi-experiment");
    expect(Object.keys(doc.jobs)).toEqual(["run-4a", "run-4b"]);
    expect(doc.jobs["run-4a"].defaults.run["working-directory"]).toBe("run-4A");
    expect(doc.jobs["run-4a"].env.OPENAI_API_KEY).toBeDefined();
    expect(doc.jobs["run-4b"].env.ANTHROPIC_API_KEY).toBeDefined();
    const last = (j) => doc.jobs[j].steps[doc.jobs[j].steps.length - 1].run;
    expect(last("run-4a")).toContain(`--run ${RUN} --trials 2 --concurrency 3`);
    expect(last("run-4b")).toContain(
      "--run a6000000-0000-4000-8000-00000000004b",
    );
    expect(text).not.toContain("vars.FI_RUN_ID");
    expect(text).not.toContain("run-experiment-");
  });
  it("commit files and the gh lines of a run", () => {
    expect(
      ciCommitFiles({ entry: catalogEntry("langgraph") }).map(
        (f) => f.fileName,
      ),
    ).toEqual(["run_and_upload.py", "agent.py"]);
    expect(
      ciCommitFiles({ entry: catalogEntry("claude_code") }).map(
        (f) => f.fileName,
      ),
    ).toEqual(["run_harness_and_upload.py"]);
    expect(
      ciSecretCommands({
        entry: catalogEntry("langgraph"),
        models: ["gpt-4.1-mini", "claude-haiku-4-5"],
        runId: RUN,
      }).split("\n"),
    ).toEqual([
      "gh secret set FI_API_KEY",
      "gh secret set FI_SECRET_KEY",
      "gh secret set OPENAI_API_KEY",
      "gh secret set ANTHROPIC_API_KEY",
      `gh variable set FI_RUN_ID --body ${RUN}`,
    ]);
  });
});
