// W7: every run is recorded in this release.
// The create body never sends `record` (the server has no such field), so
// Run is never blocked by "Runs kept off the leaderboard aren't supported",
// and the Review step no longer prints the "deleted 24 hours" summary line.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { experimentBodyOfValues } from "../experimentBody";
import { serverExperimentBody } from "../serverBody";
import {
  emptyExperimentValues,
  experimentExtras,
} from "../experimentFormValues";

const DATASET = "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
const COLUMNS = [
  { id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51", name: "question" },
];

const promptCard = () => ({
  experimentType: "llm",
  targetType: "prompt",
  promptId: "1b2c3d4e-0000-4000-8000-000000000001",
  promptVersion: "1b2c3d4e-0000-4000-8000-000000000002",
  model: [{ value: "gpt-4o-mini", providers: "openai" }],
  modelParams: {},
  configuration: { toolChoice: "auto", tools: [] },
  variables: ["question"],
  variableMapping: { question: COLUMNS[0].id },
});

const reviewStep = readFileSync(
  join(
    dirname(fileURLToPath(import.meta.url)),
    "..",
    "StepperComponentExperiment",
    "ReviewStep.jsx",
  ),
  "utf8",
);

describe.each([
  ["a stale record: false", false],
  ["record: true", true],
  ["no record value", undefined],
])("the create body, form with %s", (_label, record) => {
  const raw = {
    ...emptyExperimentValues(),
    name: "Exp W7",
    record,
    promptConfig: [promptCard()],
  };

  it("experimentExtras sends no record", () => {
    expect(experimentExtras(raw)).not.toHaveProperty("record");
  });

  it("the body has no record and Run is not blocked over it", () => {
    const body = experimentBodyOfValues(raw, { datasetId: DATASET });
    expect(body).not.toHaveProperty("record");
    const out = serverExperimentBody(body, { columns: COLUMNS });
    expect(out.body).not.toHaveProperty("record");
    expect(out.blocks.map((b) => b.field)).not.toContain("record");
    expect(out.blocks.map((b) => b.text).join(" ")).not.toMatch(/leaderboard/i);
  });
});

describe("the Review step's Settings summary (W7)", () => {
  it("prints no Record-off line and passes no form value to the switch", () => {
    expect(reviewStep).not.toContain("NOT_RECORDED_SUMMARY");
    expect(reviewStep).not.toContain("review-record-summary");
    expect(reviewStep).not.toMatch(/values\.record\b/);
    expect(reviewStep).not.toMatch(/deleted 24 hours|hours after it starts/i);
    expect(reviewStep).toMatch(
      /!isEditing && recordShown \? <RecordToggle \/>/,
    );
  });
});
