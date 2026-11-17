// options-truth L3 G4: the pure rules behind the "Your code" options:
// run-at-a-time caps (C1), key rows (C2), install lines (C4), the 1-row
// command and its note (C5), the CLI name (C6), experiment.yml (C7) and
// the sandbox form's checks (fix 9).
import { describe, expect, it } from "vitest";
import { cappedLimits, sandboxAtATimeMax } from "../snippets/sandbox";
import {
  draftReadiness,
  experimentYaml,
  limitsDefaults,
  sandboxConfigOf,
  sandboxIssues,
  sandboxSummary,
  scopeRowLabels,
  yamlHeaders,
} from "../sourceModel";
import { keyRowsOf } from "../SecretsRow";
import {
  copyAllSetup,
  copyTryNote,
  missingSteps,
  needsLine,
  setupReady,
} from "../setupRecipe";
import { modelsInText, pySample } from "../snippets/agentSamples";
import { pipPackages, pipWord, pyTracePackages } from "../snippets/python";
import { npmPackages, tsTracePackages } from "../snippets/typescript";
import { CLI, firstRow, sdkRunCommand } from "../snippets/lines";
import { snippetFor } from "../snippets";
import { MODEL_PRICES } from "../declaredUses";
import { catalogEntry } from "../catalog";
import { runCommand } from "../../Connect/connectSteps";

const RUN = "a6000000-0000-4000-8000-00000000004a";

describe("C1 · run at a time: no invented cap, every clamp named", () => {
  it("an agent that declares no concurrency has no cap", () => {
    const d = limitsDefaults({ agent: { name: "support_agent" } });
    expect(d.concurrencyMax).toBeNull();
    const l = cappedLimits({ ...d, concurrency: 8 });
    expect(l.concurrency).toBe(8);
    expect(l.capLine).toBeNull();
    expect(l.clampReason).toBeNull();
  });
  it("an agent's own cap: the default never exceeds it", () => {
    const d = limitsDefaults({ agent: { concurrency: 2 } });
    expect(d.concurrencyMax).toBe(2);
    expect(d.concurrency).toBe(2);
  });
  it("the sandbox allows min(2 × vCPU, 10) and says so", () => {
    expect([1, 2, 4, 8].map(sandboxAtATimeMax)).toEqual([2, 4, 8, 10]);
    const kept = cappedLimits({ concurrency: 8 }, { sandboxCpu: 8 });
    expect(kept.concurrency).toBe(8);
    expect(kept.capLine).toBe("The sandbox runs at most 10 at a time");
    expect(kept.clampReason).toBeNull();
    const cut = cappedLimits({ concurrency: 12 }, { sandboxCpu: 8 });
    expect(cut.concurrency).toBe(10);
    expect(cut.requestedConcurrency).toBe(12);
    expect(cut.capLine).toBe(
      "The sandbox runs at most 10 at a time (you set 12)",
    );
    expect(cut.clampReason).toBe(cut.capLine);
  });
  it("what the user set survives a smaller sandbox (vCPU 1, then 8)", () => {
    const small = cappedLimits({ concurrency: 12 }, { sandboxCpu: 1 });
    expect(small.concurrency).toBe(2);
    const big = cappedLimits(small, { sandboxCpu: 8 });
    expect(big.concurrency).toBe(10);
    expect(big.capLine).toContain("(you set 12)");
  });
  it("an agent's cap applies only with its name (its machine), else never", () => {
    const anon = cappedLimits({ concurrency: 4, concurrencyMax: 2 });
    expect(anon.concurrency).toBe(4);
    expect(anon.capLine).toBeNull();
    const own = cappedLimits(
      { concurrency: 4, concurrencyMax: 2 },
      { agentName: "support_agent", machineName: "alice-mbp" },
    );
    expect(own.concurrency).toBe(2);
    expect(own.capLine).toBe(
      "support_agent on alice-mbp runs at most 2 at a time (you set 4)",
    );
  });
});

describe("C2 · key rows: code never says no keys are needed", () => {
  it("declared models' keys first", () => {
    expect(keyRowsOf({ kind: "code", models: ["claude-haiku-4-5"] })).toEqual({
      keys: ["ANTHROPIC_API_KEY"],
      note: null,
      agentKey: false,
    });
  });
  it("no declared model: the model the agent's file calls", () => {
    const r = keyRowsOf({ kind: "code", agentModels: ["gpt-4.1-mini"] });
    expect(r.keys).toEqual(["OPENAI_API_KEY"]);
    expect(r.agentKey).toBe(true);
  });
  it("nothing known: a line, never empty, never 'No model keys needed'", () => {
    const r = keyRowsOf({ kind: "code" });
    expect(r.keys).toEqual([]);
    expect(r.note).toMatch(/agent's model key/);
    expect(keyRowsOf({ kind: "prompt" }).note).toBeNull();
  });
  it("the models a sample file calls, in order", () => {
    const known = Object.keys(MODEL_PRICES);
    expect(
      modelsInText(
        'a = ChatOpenAI(model="gpt-4.1-mini")\nb = "claude-haiku-4-5"',
        known,
      ),
    ).toEqual(["gpt-4.1-mini", "claude-haiku-4-5"]);
    expect(modelsInText("agent = build()", known)).toEqual([]);
    // provider-prefixed ids (DSPy, pydantic-ai)
    expect(modelsInText('dspy.LM("openai/gpt-4o-mini")', known)).toEqual([
      "gpt-4o-mini",
    ]);
    expect(modelsInText('Agent("openai:gpt-4.1-mini")', known)).toEqual([
      "gpt-4.1-mini",
    ]);
  });
});

describe("C3 · the LangGraph sample reads the run's params", () => {
  it("model and temperature come from config['configurable'], default gpt-4.1-mini", () => {
    const code = pySample("langgraph", {
      sandbox: { envVar: "ACME_SANDBOX_URL", tools: ["lookup_order"] },
    }).join("\n");
    expect(code).toContain(
      "def model(state: MessagesState, config: RunnableConfig):",
    );
    expect(code).toContain('params = config.get("configurable", {})');
    expect(code).toContain('model=params.get("model", "gpt-4.1-mini")');
    expect(code).toContain('temperature=params.get("temperature", 0.2)');
    // the serve file imports it as written: the sandbox URL has a default
    expect(code).toContain(
      'SANDBOX = os.environ.get("ACME_SANDBOX_URL", "http://127.0.0.1:8765")',
    );
  });
});

describe("C4 · install lines", () => {
  it("extras are one quoted shell word", () => {
    expect(pipWord("autogen-ext[openai]")).toBe('"autogen-ext[openai]"');
    expect(pipWord("requests")).toBe("requests");
    expect(pipPackages(catalogEntry("autogen"))).toContain(
      '"autogen-ext[openai]"',
    );
    expect(pipPackages(catalogEntry("ag2"))).toContain('"ag2[openai]"');
  });
  it("fi-instrumentation-otel whenever the file imports fi_instrumentation", () => {
    expect(pyTracePackages(catalogEntry("agno"))).toEqual([
      "fi-instrumentation-otel",
      "openinference-instrumentation-agno",
    ]);
    expect(pipPackages(catalogEntry("agno"))).toBe(
      "futureagi fi-instrumentation-otel openinference-instrumentation-agno requests agno",
    );
    expect(pyTracePackages(catalogEntry("langgraph"))).toEqual([
      "traceAI-langchain",
    ]);
  });
  it("TypeScript: @traceai/fi-core where the serve file imports it", () => {
    ["custom_typescript", "otel_app", "vercel_ai"].forEach((id) =>
      expect(tsTracePackages(catalogEntry(id))).toEqual(["@traceai/fi-core"]),
    );
    expect(npmPackages(catalogEntry("vercel_ai"))).toContain(
      "ai @ai-sdk/openai zod",
    );
  });
});

describe("C5 · one row and the copy note", () => {
  it("picked rows print by label; the 1-row command runs the first", () => {
    const rows = [
      { row_id: "r1" },
      { row_id: "r5", order: 4 },
      { row_id: "r12", order: 11 },
    ];
    expect(scopeRowLabels(["r5", "r12"], rows, "task")).toEqual(["T5", "T12"]);
    expect(scopeRowLabels(["r5"], rows, "row")).toEqual(["5"]);
    expect(scopeRowLabels(["zz"], rows, "task")).toEqual(["zz"]);
    expect(scopeRowLabels(["r1"], [{ row_id: "r1", label: "X1" }])).toEqual([
      "X1",
    ]);
    expect(firstRow(["T5", "T12"])).toEqual(["T5"]);
    expect(firstRow([])).toBeUndefined();
    const s = snippetFor({
      kind: "code",
      entryId: "langgraph",
      ids: { run: { runId: RUN, rows: ["T5", "T12"] } },
    });
    expect(s.tryCommand).toContain("--rows T5 --limit 1");
    expect(s.tryCommand).not.toContain("T12");
  });
  it("names only the missing setup steps, by number and title", () => {
    const opts = {
      steps: ["save", "install", "sandbox", "agent", "run"],
      titles: {
        save: "Save the runner",
        install: "Install and set keys",
        agent: "Point it at your agent",
      },
      noSandbox: true,
    };
    const missing = missingSteps({ install: true }, opts);
    expect(missing.map((x) => [x.n, x.id])).toEqual([
      [1, "save"],
      [4, "agent"],
    ]);
    expect(setupReady({ install: true }, opts)).toBe(false);
    expect(setupReady({ install: true, save: true, agent: true }, opts)).toBe(
      true,
    );
    expect(copyTryNote({ missing })).toBe(
      "Copied. Do step 1 (Save the runner) and step 4 (Point it at your agent) first",
    );
    expect(copyTryNote({ missing: [], runLabel: "#4A" })).toBe(
      "Copied. Add to experiment first; then run it in your shell and the row lands in run #4A",
    );
    expect(copyTryNote({ missing: [], added: true, runLabel: "#4A" })).toBe(
      "Copied. Run it in your shell; the row lands in run #4A",
    );
  });
  it("Copy all names the files it needs", () => {
    expect(
      needsLine([
        { file: "run_and_upload.py", n: 1 },
        { file: "sandbox_server.py", n: 3 },
        { file: "agent.py", n: 4 },
      ]),
    ).toBe(
      "# needs run_and_upload.py, sandbox_server.py and agent.py in this folder (steps 1, 3, 4)",
    );
    expect(needsLine([])).toBeNull();
    const all = copyAllSetup({
      install: "pip install futureagi",
      tryCommand: "python run_and_upload.py --limit 1",
      needs: [{ file: "run_and_upload.py", n: 1 }],
    });
    expect(JSON.stringify(all)).toContain(
      "# needs run_and_upload.py in this folder (step 1)",
    );
  });
});

describe("C6 · the CLI is futureagi", () => {
  it("run and connect builders print `futureagi …`", () => {
    expect(CLI).toBe("futureagi");
    expect(
      runCommand({
        entry: "langgraph",
        runId: RUN,
        rows: ["T2", "T5"],
        trials: 3,
      }),
    ).toBe(`futureagi run agent.py:graph --run ${RUN} --rows T2,T5 --trials 3`);
    expect(
      sdkRunCommand({
        entry: "crew.py:crew",
        runId: RUN,
        limits: { concurrency: 6 },
      }),
    ).toBe(`futureagi run crew.py:crew --run ${RUN} --concurrency 6`);
  });
});

describe("C7 · experiment.yml holds the whole sandbox and endpoint", () => {
  it("sandbox: setup, allowlist hosts, timeout", () => {
    const sandbox = sandboxConfigOf({
      source: "repo",
      repo: "acme/support-agents",
      branch: "main",
      entry: "agent.py:graph",
      setup: "pip install -r requirements.txt",
      network: "allowlist",
      allowlist: ["api.stripe.com", "api.openai.com"],
      resources: { cpu: 8, memory_gb: 16, timeout_s: 600 },
    });
    const y = experimentYaml(
      {
        kind: "code",
        execution: { mode: "sandbox" },
        sandbox,
        limits: { tries: 2 },
      },
      { runId: RUN },
    );
    [
      '  repo: "acme/support-agents"',
      '  setup: "pip install -r requirements.txt"',
      '  network: "allowlist"',
      '  allowlist: ["api.stripe.com","api.openai.com"]',
      "  cpu: 8",
      "  memory_gb: 16",
      "  timeout_s: 600",
    ].forEach((l) => expect(y.split("\n")).toContain(l));
  });
  it("endpoint: headers by secret name, streaming, audio", () => {
    expect(
      yamlHeaders([
        { name: "Authorization", secret: "ACME_API_KEY" },
        { name: "X-Env", value: "staging" },
        { name: "" },
      ]),
    ).toEqual({
      Authorization: { secret: "ACME_API_KEY" },
      "X-Env": "staging",
    });
    const y = experimentYaml(
      {
        kind: "endpoint",
        execution: { mode: "remote" },
        endpoint: {
          url: "https://agent.acme.ai/run",
          headers: [{ name: "Authorization", secret: "ACME_API_KEY" }],
          streaming: true,
          audio: "audio_in",
        },
      },
      { runId: RUN },
    );
    expect(y).toContain(
      '  headers: {"Authorization":{"secret":"ACME_API_KEY"}}',
    );
    expect(y).toContain("  streaming: true");
    expect(y).toContain('  audio: "audio_in"');
    expect(y).not.toMatch(/Bearer [^*•]/);
  });
});

describe("fix 9 · the sandbox form", () => {
  it("a zip with no file blocks Add and says so", () => {
    const c = sandboxConfigOf({ source: "zip" });
    expect(sandboxSummary(c)).toContain("zip: no file yet");
    expect(sandboxIssues(c).block).toMatch(/Choose a \.zip/);
    expect(
      draftReadiness({ mode: "sandbox", slot: { sandbox: c }, kind: "code" })
        .ok,
    ).toBe(false);
    expect(
      sandboxIssues({ ...c, zip: { name: "agent.zip" } }).block,
    ).toBeNull();
  });
  it("a runtime that cannot run the entry warns", () => {
    expect(
      sandboxIssues({ runtime: "node22", entry: "agent.py:graph" }).warn,
    ).toBe("Node 22 runs .js/.ts entries; agent.py needs a Python runtime");
    expect(
      sandboxIssues({ runtime: "python3.12", entry: "src/agent.ts:agent" })
        .warn,
    ).toBe("Python 3.12 runs .py entries; src/agent.ts needs a Node runtime");
    expect(
      sandboxIssues({ runtime: "python3.12", entry: "agent.py:graph" }).warn,
    ).toBeNull();
  });
});
