// L7b copy:absent-source-mentions / copy:script-words-on-seeded-runs /
// source:voice:providers / model:your-models: the Review's estimate help
// and tries hint name no absent source (your code, harness, voice, your
// script / runner), and provider-model judges bill that provider's key.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import {
  ESTIMATE_HELP,
  judgeProviderOf,
  runEstimateLines,
} from "../costEstimateModel";

const REMOVED =
  /your code|harness|voice|repeat each call|your script|your runner|billed by your provider\b(?! key)/i;

const reviewSource = readFileSync(
  resolve(
    process.cwd(),
    "src/sections/develop-detail/Experiment/StepperComponentExperiment/ReviewStep.jsx",
  ),
  "utf8",
);

describe("L7b estimate copy", () => {
  it("How is this estimated? names no removed path", () => {
    ESTIMATE_HELP.forEach((line) => expect(line).not.toMatch(REMOVED));
    expect(ESTIMATE_HELP[0]).toBe(
      "Tries = rows × each run's tries (its own Tries, else Trials).",
    );
    // v2.1 (estimate:details): built from the runs; Image is a hidden type.
    expect(ESTIMATE_HELP.join(" ")).not.toMatch(/\bimages?\b/i);
  });

  it("the Review tries hint is one line for every run's card", () => {
    expect(reviewSource).toContain(
      "Set on each run&apos;s card (Tries per row).",
    );
    expect(reviewSource).not.toMatch(/Repeat each call|voice config|allVoice/);
  });

  const judgesLine = (judgeModels) =>
    runEstimateLines({
      cards: [
        {
          targetType: "prompt",
          promptId: "p1",
          name: "Support Reply",
          model: [{ value: "gpt-4o-mini" }],
        },
      ],
      rows: 50,
      trials: 2,
      ordinal: 3,
      estimate: {
        judge_cost_usd: 0.81,
        judge_calls: 100,
        judge_models: judgeModels,
      },
    }).judges;

  it("provider-model judges bill that provider's key; turing judges are Future AGI's", () => {
    expect(judgesLine(["gpt-4o"])).toBe(
      "Judges billed to your OpenAI key ≈ $0.81 (100 judge calls at gpt-4o)",
    );
    expect(judgesLine(["claude-sonnet-4", "claude-haiku-4"])).toMatch(
      /^Judges billed to your Anthropic key/,
    );
    expect(judgesLine(["gpt-4o", "gemini-2.5-pro"])).toMatch(
      /^Judges billed to your provider keys/,
    );
    expect(judgesLine(["acme-judge-b7"])).toMatch(
      /^Judges billed to your provider keys/,
    );
    expect(judgesLine(["turing_large"])).toMatch(/^Future AGI judges ≈/);
    expect(judgesLine(["turing_large", "gpt-4o"])).toMatch(
      /^Judges \(Future AGI and your OpenAI key\)/,
    );
    expect(judgeProviderOf("openai/gpt-4.1")).toBe("OpenAI");
  });
});

describe("L7b cost:review · code evals and task checks are free", () => {
  it("reads the eval's type from its template and a check's key", async () => {
    const { billsJudgeCall } = await import("../estimateInputs");
    // The wizard card shape (EvaluationStepExperimentCreation entry).
    const card = (templateDetails, extra = {}) => ({
      templateId: "t1",
      evalTemplateName: "x",
      templateDetails,
      ...extra,
    });
    expect(billsJudgeCall(card({ eval_type: "code", name: "is_json" }))).toBe(
      false,
    );
    expect(billsJudgeCall(card({ eval_type: "llm", name: "tone" }))).toBe(true);
    expect(billsJudgeCall(card({ eval_type: "agent" }))).toBe(true);
    expect(billsJudgeCall(card({ key: "check:address_updated" }))).toBe(false);
    expect(billsJudgeCall({ key: "check:refund_once" })).toBe(false);
    expect(billsJudgeCall({ eval_type: "check" })).toBe(false);
    expect(billsJudgeCall({})).toBe(true);
  });
});
