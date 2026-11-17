/**
 * options-truth L3 G8 P6: Edit names what changed per run; Start from,
 * Duplicate & edit and Edit give R2-era runs (#1C My code, #1D harness) back
 * as R4 cards that keep what they ran; the copied evals are the ones the
 * source experiment was scored with.
 */
import { describe, expect, it } from "vitest";
import {
  editChangeSummary,
  evalIdsOfExperiment,
  r4ConfigOfLegacy,
  r4DraftOfRun,
  runChangesOf,
  notCopiedLine,
  valuesFromExperiment,
} from "../experimentFormValues";
import { flattenRuns } from "../runPlan";

const LEGACY_CODE = {
  kind: "code",
  code: {
    mode: "sdk_preview",
    host: "alice@mbp-14",
    framework: "LangGraph 0.6",
  },
  model: { id: "gpt-4o", params: { temperature: 0 } },
  trials: 3,
};
const LEGACY_HARNESS = {
  kind: "harness",
  harness: {
    name: "Codex CLI",
    model: "gpt-5-codex",
    sandbox: "Docker · acme-support-sandbox:1.4",
    mcp_servers: [{ name: "acme-support-sandbox", tools: 12 }],
  },
  model: { id: "gpt-5-codex", params: {} },
};

describe("R2-era runs become R4 cards", () => {
  it("a My code run keeps its framework, version, model and machine", () => {
    const d = r4DraftOfRun({
      name: "LangGraph support agent",
      config: LEGACY_CODE,
    });
    expect(d).not.toBeNull();
    expect(d.kind).toBe("code");
    expect(d.entryId).toBe("langgraph");
    expect(d.mode).toBe("local");
    expect(d.form).toBe("upload");
    expect(d.config.framework).toMatchObject({
      id: "langgraph",
      version: "0.6",
    });
    expect(d.config.execution.label).toBe("alice@mbp-14");
    expect(d.declared.model).toBe("gpt-4o");
    expect(d.uses.models).toEqual([{ name: "gpt-4o" }]);
  });

  it("a harness run keeps its harness, model, servers and Docker", () => {
    const d = r4DraftOfRun({ name: "Codex", config: LEGACY_HARNESS });
    expect(d.kind).toBe("harness");
    expect(d.entryId).toBe("codex_cli");
    expect(d.mode).toBe("local");
    expect(d.declared.model).toBe("gpt-5-codex");
    expect(d.config.mcp_servers).toEqual([
      { name: "acme-support-sandbox", tools: 12 },
    ]);
    const sandboxed = r4ConfigOfLegacy(
      {
        ...LEGACY_HARNESS,
        harness: {
          ...LEGACY_HARNESS.harness,
          sandbox: "Future AGI sandbox · x",
        },
      },
      { id: "codex_cli" },
    );
    expect(sandboxed.execution.mode).toBe("sandbox");
  });

  it("R4 configs come back untouched", () => {
    const r4 = {
      kind: "harness",
      harness: { id: "claude_code", name: "Claude Code" },
      execution: { mode: "sandbox" },
    };
    expect(r4ConfigOfLegacy(r4, { id: "claude_code" })).toBe(r4);
    const code = { kind: "code", framework: { id: "crewai" } };
    expect(r4ConfigOfLegacy(code, { id: "crewai" })).toBe(code);
  });

  // dataset integration FE-0 (review must-fix 2): a copy takes only offered kinds; a
  // code or harness run is counted as not copied (no card, no script).
  it("a duplicated experiment copies no code or harness run and says so", () => {
    const v = valuesFromExperiment(
      {
        name: "#1",
        runs: [
          {
            run_id: "R1C",
            name: "LangGraph",
            target_type: "code",
            config: LEGACY_CODE,
          },
          {
            run_id: "R1E",
            name: "Codex",
            target_type: "harness",
            config: LEGACY_HARNESS,
          },
        ],
      },
      { mode: "duplicate" },
    );
    expect(v.promptConfig).toEqual([]);
    expect(v.notCopied).toBe(2);
    expect(notCopiedLine(v.notCopied)).toBe(
      "2 runs not copied: they ran outside Future AGI, so there is nothing here to start again.",
    );
    // L7b G2: the skipped runs are named (a span when consecutive).
    expect(notCopiedLine(6, ["#3A", "#3B", "#3C", "#3D", "#3E", "#3F"])).toBe(
      "6 runs not copied (#3A–#3F): they ran outside Future AGI, so there is nothing here to start again.",
    );
    expect(notCopiedLine(1, ["#3C"])).toBe(
      "1 run not copied (#3C): it ran outside Future AGI, so there is nothing here to start again.",
    );
    expect(notCopiedLine(0)).toBeNull();
  });

  it("Edit keeps every run (dropping one would delete it on save)", () => {
    const v = valuesFromExperiment(
      {
        name: "#1",
        runs: [
          {
            run_id: "R1C",
            name: "LangGraph",
            target_type: "code",
            config: LEGACY_CODE,
          },
        ],
      },
      { mode: "edit" },
    );
    expect(v.promptConfig.map((c) => c.r4?.entryId)).toEqual(["langgraph"]);
    expect(v.promptConfig[0].runId).toBe("R1C");
    expect(v.notCopied).toBe(0);
  });
});

describe("Edit names each change per run", () => {
  const detail = {
    name: "e",
    user_eval_metrics: [],
    runs: [
      {
        run_id: "RA",
        label: "#4A",
        letter: "A",
        experiment_ordinal: 4,
        target_type: "agent",
        target: { graph_id: "G", version_id: "GV3", version_name: "v3" },
      },
      {
        run_id: "RB",
        label: "#4B",
        letter: "B",
        experiment_ordinal: 4,
        target_type: "prompt",
        target: {
          prompt_id: "P",
          prompt_version_id: "PV3",
          prompt_version_name: "V3",
          model: "gpt-4o",
          model_params: { temperature: 0.2 },
        },
      },
    ],
  };
  const summarise = (edit) => {
    const original = valuesFromExperiment(detail, { mode: "edit" });
    const current = structuredClone(original);
    edit(current);
    return editChangeSummary(original, current, {
      runs: detail.runs,
      flatten: flattenRuns,
    });
  };

  it("an agent version change says version, not params", () => {
    const s = summarise((c) => {
      c.promptConfig[0].agentVersion = "GV2";
      c.promptConfig[0].versionLabel = "v2";
    });
    expect(s.text).toBe("Re-run: #4A (version v3 → v2) · Kept: #4B");
  });

  it("a prompt run names version, model and params", () => {
    const s = summarise((c) => {
      const p = c.promptConfig[1];
      p.promptVersion = "PV2";
      p.versionLabel = "V2";
      p.model[0].value = "gpt-4.1";
      p.modelParams = { "gpt-4.1": { temperature: 0.7 } };
    });
    expect(s.changes["#4B"]).toEqual([
      "version V3 → V2",
      "model gpt-4o → gpt-4.1",
      "params temperature 0.2 → 0.7",
    ]);
    expect(s.kept).toEqual(["#4A"]);
  });

  it("an added R4 card is New with the next letter", () => {
    const s = summarise((c) => {
      c.promptConfig.push({
        targetType: "harness",
        r4: { entryId: "codex_cli" },
        runName: "Codex CLI",
      });
    });
    expect(s.added).toEqual(["#4C"]);
    expect(s.text).toBe("New: #4C · Kept: #4A, #4B");
  });

  it("R4 cards name model and where they run", () => {
    const a = {
      item: {
        r4: {
          entryName: "Claude Code",
          whereName: "Docker",
          config: { harness: { model: "claude-sonnet-4-5" } },
        },
      },
    };
    const b = {
      item: {
        r4: {
          entryName: "Claude Code",
          whereName: "the Future AGI sandbox",
          config: { harness: { model: "claude-opus-4-1" } },
        },
      },
    };
    expect(runChangesOf(a, b)).toEqual([
      "model claude-sonnet-4-5 → claude-opus-4-1",
      "runs in Docker → the Future AGI sandbox",
    ]);
    expect(runChangesOf(a, a)).toEqual([]);
  });
});

describe("evalIdsOfExperiment", () => {
  const r1 = {
    results: [
      {
        experiment_id: "E3",
        metric_keys: ["eval:task_success", "eval:reply_rubric"],
      },
      { experiment_id: "E2", metric_keys: ["eval:other"] },
    ],
    metric_defs: [
      { key: "eval:task_success", user_eval_id: "U1" },
      { key: "eval:reply_rubric", user_eval_id: "U2" },
      { key: "eval:other", user_eval_id: "U3" },
      { key: "ops:cost" },
    ],
  };
  it("maps the experiment's metric keys to dataset eval ids", () => {
    expect(evalIdsOfExperiment(r1, "E3")).toEqual(["U1", "U2"]);
    expect(evalIdsOfExperiment(r1, "E2")).toEqual(["U3"]);
    expect(evalIdsOfExperiment(r1, "nope")).toEqual([]);
    expect(evalIdsOfExperiment(null, "E3")).toEqual([]);
  });
});
