// options-truth L1 G2 (S3): every command any generator prints uses only
// the one grammar's file names and flags. Collects the commands from the
// code, CI, upload, Start it, waiting, How to run, harness and voice
// generators and checks each token.
import { describe, expect, it } from "vitest";
import { catalogEntry } from "../catalog";
import { FRAMEWORKS } from "../catalogEntries";
import { snippetFor } from "../snippets";
import {
  CI_FILE,
  RUNNER_EXTRA_FLAGS,
  RUNNER_FILES,
  RUNNER_FLAGS,
  SDK_RUN_FLAGS,
  runFlagsOf,
  runnerCommand,
  sdkRunCommand,
  shellWord,
} from "../snippets/lines";
import {
  ciRunCommand,
  ciSecrets,
  ciWorkflow,
  ciWorkflowPath,
} from "../snippets/ci";
import { runStartCommand, startItPlan } from "../snippets/startIt";
import { SANDBOX_COMMAND, sandboxServerScript } from "../snippets/sandbox";
import {
  experimentYaml,
  runConfigFromDraft,
  sandboxConfigOf,
  sandboxSummary,
} from "../sourceModel";
import { MODEL_LATENCY_MS, MODEL_PRICES, vendorKeysOf } from "../declaredUses";
import { attemptsOf, simulateBody } from "../TodayStatus";
import { buildUploadSnippets } from "src/sections/develop-detail/Experiment/uploadSnippets";
import { waitingOf } from "src/sections/develop-detail/Experiment/waitingModel";

const RUN = "a6000000-0000-4000-8000-00000000004a";
const IDS = {
  datasetId: "d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06",
  datasetName: "Support ops tasks · sandbox",
  experimentId: "e0000000-0000-4000-8000-000000000004",
  runId: RUN,
  runLabel: "#4A",
  inputColumn: "instruction",
  tries: 3,
  sandbox: {
    name: "acme-support-sandbox",
    envVar: "ACME_SANDBOX_URL",
    port: 8080,
    tools: ["lookup_order", "issue_refund"],
    tables: ["orders"],
  },
};

const FILES = new Set([...Object.values(RUNNER_FILES), "experiment.yml"]);
const FLAGS = new Set([...RUNNER_FLAGS, ...RUNNER_EXTRA_FLAGS.harness]);

/** Check one runner / SDK command line against the grammar. */
function checkLine(line) {
  const words = line.trim().split(/\s+/);
  // S3 / I5: the CLI is `futureagi` (`fi` is a shell reserved word)
  expect(words[0], line).not.toBe("fi");
  if (words[0] === "futureagi") {
    // dataset integration FE-0: there is no `futureagi connect`; only `run` is printed.
    const sub = words[1];
    const allowed = SDK_RUN_FLAGS;
    expect(["run"], line).toContain(sub);
    words
      .filter((w) => w.startsWith("--"))
      .forEach((f) => expect(allowed, `${f} in ${line}`).toContain(f));
    return;
  }
  const file = words[0] === "npx" ? words[2] : words[1];
  const name = file.split("/").pop();
  expect(FILES.has(name), `${file} in ${line}`).toBe(true);
  const own =
    name === RUNNER_FILES.sandbox
      ? new Set(RUNNER_EXTRA_FLAGS.sandbox)
      : /^upload_results/.test(name)
        ? new Set([...FLAGS, ...RUNNER_EXTRA_FLAGS.upload])
        : FLAGS;
  words
    .filter((w) => w.startsWith("--"))
    .forEach((f) => expect(own.has(f), `${f} in ${line}`).toBe(true));
  // a run is its uuid, never an unquoted `#2C`
  expect(line).not.toMatch(/--run #/);
}

/** Every runner / SDK command inside a text (scripts, YAML, comments). */
function commandsIn(text) {
  return String(text ?? "")
    .split("\n")
    .map((l) => l.replace(/^\s*(#|\/\/)?\s*(run: |- run: )?/, ""))
    .filter((l) => /^(python|npx tsx|(fi|futureagi) (run|connect)) /.test(l))
    .filter((l) =>
      /(run_|upload_|serve_|sandbox_|\.py|\.ts|fi |futureagi )/.test(l),
    )
    .map((l) => l.replace(/\s+#.*$/, "").replace(/ &$/, ""));
}

describe("S3 grammar · every generator", () => {
  // dataset integration FE-0: Your code is absent from the picker, but its generators
  // stay until FE-1 replaces them, so the grammar still holds for them.
  const codeEntries = FRAMEWORKS;
  it.each(codeEntries.map((e) => [e.id, e]))("%s: code snippets", (_, e) => {
    const lines = [];
    (e.languages ?? ["python"]).forEach((lang) => {
      const s = snippetFor({ kind: "code", entryId: e.id, lang, ids: IDS });
      [
        s.tryCommand,
        s.runCommand,
        s.dryRunCommand,
        s.mockCommand,
        s.noSandboxCommand,
      ]
        .filter(Boolean)
        .forEach((c) => lines.push(c));
      lines.push(...commandsIn(s.file));
      s.steps.forEach((st) => lines.push(...commandsIn(st.code)));
    });
    expect(lines.length).toBeGreaterThan(3);
    lines.forEach(checkLine);
  });

  it("CI, upload, Start it and sandbox snippets", () => {
    const e = catalogEntry("langgraph");
    const texts = [
      ciWorkflow({ entry: e, ids: IDS, limits: { tries: 3, concurrency: 4 } }),
      ciWorkflow({ entry: catalogEntry("claude_code"), ids: IDS }),
      ...Object.values(buildUploadSnippets({ experimentId: "e", runId: RUN })),
      startItPlan({
        ordinal: 4,
        runs: [
          {
            label: "#4A",
            name: "a",
            kind: "code",
            entryId: "langgraph",
            ids: IDS,
          },
        ],
        sandbox: IDS.sandbox,
      }).shell,
      sandboxServerScript(IDS.sandbox),
    ];
    const lines = texts.flatMap(commandsIn);
    lines.push(
      runStartCommand({
        label: "#4A",
        kind: "code",
        entryId: "mastra",
        lang: "typescript",
        ids: IDS,
        useFolder: false,
      }).command,
      runStartCommand({
        label: "#4B",
        kind: "harness",
        entryId: "claude_code",
        ids: IDS,
        useFolder: false,
      }).command,
      sdkRunCommand({
        entry: "agent.py:graph",
        runId: RUN,
        rows: "failed",
        trials: 3,
        params: { model: "gpt-4.1" },
        watch: true,
      }),
      SANDBOX_COMMAND,
    );
    expect(lines.length).toBeGreaterThan(12);
    lines.forEach(checkLine);
    // S3 (L3): each run's CI job is its own file, never Start it's
    texts.slice(0, 2).forEach((t) => {
      expect(t).toContain(ciWorkflowPath(IDS.runLabel));
      expect(t).not.toContain(`.github/workflows/${CI_FILE}`);
    });
    expect(texts.join("\n")).not.toMatch(
      /--fail-if|--temperature|--only |--resume|run-and-upload|serve-agent/,
    );
  });
});

describe("runnerCommand · sdkRunCommand", () => {
  it("prints every knob as its S3 flag, quoted when needed", () => {
    expect(
      runnerCommand({
        entry: "agent.py:graph",
        runId: RUN,
        rows: "failed",
        trials: 3,
        limits: { concurrency: 4, max_cost_usd: 5, timeout_s: 120 },
        params: { temperature: 0.2, system_prompt: "Be brief" },
        sideEffects: "stubs",
        dryRun: true,
      }),
    ).toBe(
      `python run_and_upload.py --agent agent.py:graph --run ${RUN} --rows failed --trials 3 --concurrency 4 --max-cost-usd 5 --timeout-s 120 --param temperature=0.2 --param 'system_prompt=Be brief' --side-effects stubs --dry-run`,
    );
    expect(
      runnerCommand({
        lang: "typescript",
        entry: "src/agent.ts:agent",
        limit: 1,
        dryRun: true,
        noSandbox: true,
      }),
    ).toBe(
      "npx tsx run_and_upload.ts --agent src/agent.ts:agent --limit 1 --dry-run --no-sandbox",
    );
    expect(runnerCommand({ lang: "harness", runId: RUN, trials: 2 })).toBe(
      `python run_harness_and_upload.py --workdir . --run ${RUN} --trials 2`,
    );
    // dataset integration FE-0: the voice runner's --depth is gone
    expect(
      runnerCommand({ entry: "agent.py:reply", depth: "full_voice" }),
    ).not.toContain("full_voice");
    expect(shellWord("#2C")).toBe("'#2C'");
    expect(shellWord("${{ vars.FI_RUN_ID }}")).toBe("${{ vars.FI_RUN_ID }}");
  });
  it("futureagi run takes --run --rows --trials, the limits, --param and --watch", () => {
    expect(
      sdkRunCommand({
        entry: "agent.py:target",
        runId: RUN,
        rows: "new",
        trials: 2,
        watch: true,
      }),
    ).toBe(
      `futureagi run agent.py:target --run ${RUN} --rows new --trials 2 --watch`,
    );
    // S3: every limit the screen set rides on the line
    expect(
      sdkRunCommand({
        entry: "agent.py:target",
        runId: RUN,
        rows: ["T5", "T12"],
        trials: 3,
        limits: { concurrency: 8, max_cost_usd: 2.5, timeout_s: 90 },
        params: { model: "claude-haiku-4-5" },
      }),
    ).toBe(
      `futureagi run agent.py:target --run ${RUN} --rows T5,T12 --trials 3 --concurrency 8 --max-cost-usd 2.5 --timeout-s 90 --param model=claude-haiku-4-5`,
    );
  });
});

describe("CI by vendor and settings", () => {
  it("secrets follow the models' vendors", () => {
    expect(
      ciSecrets({
        entry: catalogEntry("langgraph"),
        models: ["claude-sonnet-4-5", "gemini-2.5-pro"],
      }),
    ).toEqual([
      "FI_API_KEY",
      "FI_SECRET_KEY",
      "ANTHROPIC_API_KEY",
      "GEMINI_API_KEY",
    ]);
    expect(vendorKeysOf(["gpt-4.1", "o4-mini", "gpt-5-codex"])).toEqual([
      "OPENAI_API_KEY",
    ]);
  });
  it("the trigger and the gate change the file", () => {
    const e = catalogEntry("langgraph");
    const push = ciWorkflow({
      entry: e,
      ids: IDS,
      ci: { trigger: "push", threshold: 0.7 },
      limits: { tries: 2, concurrency: 3 },
    });
    expect(push).toContain("  push:\n    branches: [main]");
    expect(push).toContain("--trials 2 --concurrency 3 --fail-under 0.7");
    expect(push).not.toMatch(/Preview|Works today|not released|PREVIEW/);
  });
});

describe("run config (C3) and experiment.yml", () => {
  const draft = {
    entryId: "langgraph",
    mode: "sandbox",
    form: "upload",
    limits: { tries: 3, concurrency: 2, maxCostUsd: 4, timeoutS: 60 },
    params: { temperature: 0.3 },
    safety: "stubbed",
    declared: {
      model: "gpt-4.1",
      tools: ["issue_refund"],
      mcp: ["acme-support-sandbox"],
    },
    sandbox: IDS.sandbox,
    slot: {
      sandbox: sandboxConfigOf({
        source: "image",
        image: "ghcr.io/acme/a:1",
        network: "allowlist",
        allowlist: ["api.acme.ai", "x.io"],
        resources: { cpu: 4, memory_gb: 8 },
      }),
    },
  };
  const cfg = runConfigFromDraft(draft);
  it("declared uses reach models, tools and mcp_servers", () => {
    expect(cfg.models).toEqual([
      { id: "gpt-4.1", role: "main", calls: null, source: "declared" },
    ]);
    expect(cfg.tools).toEqual([
      {
        name: "issue_refund",
        declared: true,
        called: false,
        side_effect: true,
      },
    ]);
    expect(cfg.mcp_servers).toEqual([
      {
        name: "acme-support-sandbox",
        transport: "http",
        url: "$ACME_SANDBOX_URL/mcp",
        tools: ["lookup_order", "issue_refund"],
      },
    ]);
    expect(cfg.safety.side_effects).toBe("stubs");
  });
  it("the sandbox settings are the run's identity", () => {
    expect(cfg.execution.mode).toBe("sandbox");
    expect(cfg.sandbox.resources.cpu).toBe(4);
    expect(sandboxSummary(cfg.sandbox)).toBe(
      "ghcr.io/acme/a:1 · agent.py:graph · Python 3.12 · 4 vCPU / 8 GB · network allowlist (2)",
    );
    expect(
      sandboxSummary(
        sandboxConfigOf({
          repo: "acme/s",
          branch: "dev",
          resources: { cpu: 2, memory_gb: 4 },
        }),
      ),
    ).toBe("acme/s@dev · Python 3.12 · 2 vCPU / 4 GB · network off");
  });
  it("experiment.yml carries rows, tries, limits, params, safety and evals", () => {
    const y = experimentYaml(cfg, {
      runId: RUN,
      rows: ["r1", "r2"],
      split: "smoke",
      evals: ["task_completion"],
    });
    expect(y).toContain("#   python run_and_upload.py --config experiment.yml");
    expect(y).toContain('rows:\n  - "r1"\n  - "r2"');
    expect(y).toContain("trials: 3");
    expect(y).toContain(
      "limits:\n  concurrency: 2\n  max_cost_usd: 4\n  timeout_s: 60",
    );
    expect(y).toContain("params:\n  temperature: 0.3");
    expect(y).toContain('safety:\n  side_effects: "stubs"');
    expect(y).toContain('evals:\n  - "task_completion"');
    expect(y).toContain(`run_id: "${RUN}"`);
  });
});

describe("waiting, How to run, Simulate (C15)", () => {
  it("waits by mode", () => {
    const base = { run_id: RUN, label: "#4A" };
    expect(
      waitingOf({
        ...base,
        config: {
          kind: "code",
          framework: { id: "langgraph" },
          execution: { mode: "local" },
        },
      }).title,
    ).toBe("#4A waits for your terminal");
    const ci = waitingOf({
      ...base,
      config: {
        kind: "code",
        framework: { id: "langgraph" },
        execution: { mode: "ci" },
      },
    });
    expect(ci.title).toBe("#4A waits for the CI job");
    expect(ci.simulate.label).toBe("Simulate the CI job");
    // dataset integration FE-0: a waiting run prints no generated runner or workflow
    expect(ci.command).toBeNull();
    expect(waitingOf({ ...base, kind: "upload" }).title).toBe(
      "#4A waits for results",
    );
    expect(
      waitingOf({ ...base, config: { execution: { mode: "remote" } } }),
    ).toBeNull();
    expect(
      waitingOf({
        ...base,
        config: {
          kind: "code",
          execution: { mode: "sandbox" },
          sandbox: sandboxConfigOf({ repo: "a/b", branch: "main" }),
        },
      }).title,
    ).toBe(
      "Starting sandbox · a/b@main · Python 3.12 · 2 vCPU / 4 GB · network off",
    );
  });
  it("Simulate sends the draft's config, tries and scope", () => {
    expect(
      simulateBody({
        simulateRows: 1,
        datasetId: "d",
        entryId: "langgraph",
        tries: 3,
        config: { kind: "code" },
        rowIds: ["r1"],
        as: "ci_job",
      }),
    ).toEqual({
      rows: 1,
      as: "ci_job",
      dataset_id: "d",
      entry_id: "langgraph",
      tries: 3,
      config: { kind: "code" },
      row_ids: ["r1"],
    });
    expect(
      simulateBody({ simulateRows: "all", tries: 1, splitIds: ["s1"] })
        .split_ids,
    ).toEqual(["s1"]);
    expect(attemptsOf(30, 3)).toBe(90);
    expect(attemptsOf(1, 3)).toBe(3);
  });
});

describe("prices and latency (C10)", () => {
  it("cover every model a picker offers", () => {
    [
      "claude-opus-4-1",
      "gpt-5-codex",
      "o4-mini",
      "gpt-4o-mini-tts",
      "gpt-4o-transcribe",
      "gpt-image-1",
    ].forEach((m) => {
      expect(MODEL_PRICES[m], m).toBeTruthy();
      expect(MODEL_LATENCY_MS[m], m).toBeGreaterThan(0);
    });
    Object.keys(MODEL_PRICES).forEach((m) =>
      expect(MODEL_LATENCY_MS[m], m).toBeGreaterThan(0),
    );
  });
});

// options-truth L2 G4 (C1, S3): what the drawer form sets rides on every
// command it prints, from one builder (runFlagsOf → runnerCommand).
describe("runFlagsOf: the form's flags on every printed command", () => {
  const SCHEMA = [
    { name: "model", type: "str", default: "gpt-4.1-mini" },
    { name: "temperature", type: "float", default: 0.2 },
    { name: "retriever_k", type: "int", default: 5 },
  ];
  const form = (o = {}) =>
    runFlagsOf({
      runId: RUN,
      limits: { tries: 3, concurrency: 2, maxCostUsd: 0.3, timeoutS: 90 },
      params: { model: "claude-haiku-4-5", temperature: 1.4, retriever_k: 2 },
      schema: SCHEMA,
      ...o,
    });

  it("LangGraph: tries, at a time, cap, time limit and changed params", () => {
    const s = snippetFor({
      kind: "code",
      entryId: "langgraph",
      ids: { ...IDS, run: form() },
    });
    expect(s.runCommand).toBe(
      `python run_and_upload.py --agent agent.py:graph --run ${RUN} --trials 3 --concurrency 2 --max-cost-usd 0.3 --timeout-s 90 --param model=claude-haiku-4-5 --param temperature=1.4 --param retriever_k=2`,
    );
    // the 1-row copy is the same builder: same flags plus --limit 1
    expect(s.tryCommand).toBe(
      `python run_and_upload.py --agent agent.py:graph --run ${RUN} --limit 1 --trials 3 --concurrency 2 --max-cost-usd 0.3 --timeout-s 90 --param model=claude-haiku-4-5 --param temperature=1.4 --param retriever_k=2`,
    );
  });

  it("a param left at its default is not printed; picked rows are --rows", () => {
    const f = form({
      params: { model: "gpt-4.1-mini", temperature: 0.7, retriever_k: 5 },
      rowIds: ["T4", "T26"],
    });
    expect(f.params).toEqual({ temperature: 0.7 });
    const s = snippetFor({
      kind: "code",
      entryId: "langgraph",
      ids: { ...IDS, run: f },
    });
    expect(s.runCommand).toContain("--rows T4,T26 ");
    expect(s.runCommand).toContain("--param temperature=0.7");
    expect(s.runCommand).not.toContain("--param model=");
  });

  it("a skipped sandbox adds --no-sandbox to the 1-row copy (sandbox suites)", () => {
    const sandbox = { envVar: "ACME_SANDBOX_URL", tools: ["lookup_order"] };
    const on = snippetFor({
      kind: "code",
      entryId: "langgraph",
      ids: { ...IDS, sandbox, run: form({ noSandbox: true }) },
    });
    expect(on.tryCommand).toMatch(/ --no-sandbox$/);
    expect(on.runCommand).toMatch(/ --no-sandbox$/);
    const off = snippetFor({
      kind: "code",
      entryId: "langgraph",
      ids: { ...IDS, sandbox, run: form() },
    });
    expect(off.tryCommand).not.toContain("--no-sandbox");
  });

  it("TypeScript prints the same flags", () => {
    const s = snippetFor({
      kind: "code",
      entryId: "mastra",
      lang: "typescript",
      ids: { ...IDS, run: form() },
    });
    expect(s.runCommand).toContain(
      `--run ${RUN} --trials 3 --concurrency 2 --max-cost-usd 0.3 --timeout-s 90 --param model=claude-haiku-4-5`,
    );
  });

  it("Start it prints exactly the drawer's command for that run", () => {
    const flags = form();
    const drawer = snippetFor({
      kind: "code",
      entryId: "langgraph",
      ids: { ...IDS, run: flags },
    });
    const c = runStartCommand({
      label: "#4A",
      kind: "code",
      entryId: "langgraph",
      ids: { ...IDS, tries: 3 },
      limits: { concurrency: 2, maxCostUsd: 0.3, timeoutS: 90 },
      flags,
      useFolder: false,
    });
    expect(c.command).toBe(drawer.runCommand);
  });

  it("the CI run line carries the cap, the time limit and the params", () => {
    const cmd = ciRunCommand({
      entry: catalogEntry("langgraph"),
      limits: { tries: 3, concurrency: 2, maxCostUsd: 0.3, timeoutS: 90 },
      flags: form(),
    });
    expect(cmd).toBe(
      "python run_and_upload.py --agent agent.py:graph --run ${{ vars.FI_RUN_ID }} --trials 3 --concurrency 2 --max-cost-usd 0.3 --timeout-s 90 --param model=claude-haiku-4-5 --param temperature=1.4 --param retriever_k=2 --fail-under 0.85",
    );
  });
});
