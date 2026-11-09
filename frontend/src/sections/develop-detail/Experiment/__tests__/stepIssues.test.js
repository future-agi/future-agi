import { describe, expect, it } from "vitest";
import {
  NAME_TAKEN_MESSAGE,
  configurationIssues,
  httpUrlHint,
  dataIssues,
  evaluationIssues,
  reviewIssues,
  stepIssues,
} from "../stepIssues";

const columns = [
  { id: "c1", name: "customer_message" },
  { id: "c2", name: "customer_tier" },
];
const agent = {
  experimentType: "llm",
  targetType: "agent",
  agentId: "g",
  agentVersion: "v3",
  name: "Support Agent",
  variables: ["user_message", "customer_tier"],
  variableMapping: { user_message: "", customer_tier: "c2" },
};
const prompt = {
  experimentType: "llm",
  targetType: "prompt",
  promptId: "p",
  promptVersion: "v5",
  name: "Support Reply",
  model: [{ value: "gpt-4o" }],
  variables: ["customer_message", "customer_tier"],
  variableMapping: { customer_message: "c1", customer_tier: "c2" },
};

describe("stepIssues", () => {
  it("checks the name and that runs exist", () => {
    expect(
      configurationIssues({
        name: "",
        experimentType: "llm",
        promptConfig: [],
      }),
    ).toEqual(["Name is required", "Add at least one run"]);
    expect(
      configurationIssues(
        { name: "x", experimentType: "llm", promptConfig: [prompt] },
        { nameTaken: true },
      ),
    ).toEqual([NAME_TAKEN_MESSAGE]);
  });

  it("checks each run target", () => {
    const issues = configurationIssues({
      name: "x",
      experimentType: "llm",
      promptConfig: [
        { ...prompt, isDraft: true, model: [] },
        { targetType: "external", runName: " " },
        { targetType: "http", http: { url: "http://x", outputPath: "" } },
      ],
    });
    expect(issues).toEqual([
      "Pick a saved version of Support Reply",
      "Pick a model for Support Reply",
      "Run name is required",
      "Enter a valid https:// URL",
      "Output path is required, e.g. $.reply",
    ]);
  });

  it("caps runs at 12", () => {
    const many = {
      ...prompt,
      model: Array.from({ length: 13 }, (_, i) => ({ value: `m${i}` })),
    };
    expect(
      configurationIssues({
        name: "x",
        experimentType: "llm",
        promptConfig: [many],
      }),
    ).toContain("Up to 12 runs per experiment");
  });

  it("names unmapped variables with run letters", () => {
    expect(
      dataIssues(
        { experimentType: "llm", promptConfig: [agent, prompt] },
        { columns },
      ),
    ).toEqual([
      "A: {{user_message}} has no column. Pick a column, or leave it empty.",
    ]);
    expect(
      dataIssues(
        { experimentType: "llm", promptConfig: [{ targetType: "external" }] },
        { columns },
      ),
    ).toEqual([]);
  });

  it("requires an evaluation and valid settings", () => {
    expect(evaluationIssues({ userEvalMetrics: [] })).toEqual([
      "Add at least one evaluation",
    ]);
    expect(
      reviewIssues({
        trials: 11,
        maxConcurrency: 0,
        metadata: [
          { key: "", value: "x" },
          { key: "a", value: "1" },
          { key: "a", value: "2" },
        ],
      }),
    ).toEqual([
      "Trials must be 1–10",
      "Concurrency must be 1–50",
      "Every metadata value needs a key",
      "Metadata keys must be unique",
    ]);
    expect(
      stepIssues(
        {
          name: "n",
          experimentType: "llm",
          promptConfig: [prompt],
          userEvalMetrics: [{}],
          trials: 3,
          maxConcurrency: 10,
          metadata: [],
        },
        { columns },
      ),
    ).toEqual([[], [], [], []]);
  });
});

describe("httpUrlHint (CR#32)", () => {
  it("says nothing for empty or a public https URL", () => {
    expect(httpUrlHint("")).toBeNull();
    expect(httpUrlHint("https://api.acme.ai/reply")).toBeNull();
  });
  it("asks for a full https URL", () => {
    expect(httpUrlHint("not a url")).toMatchObject({ level: "error" });
    expect(httpUrlHint("http://api.acme.ai/reply")).toMatchObject({
      level: "error",
      text: expect.stringContaining("https://api.acme.ai/reply"),
    });
  });
  it("warns that localhost and private hosts are unreachable", () => {
    for (const u of [
      "http://localhost:8000/reply",
      "https://127.0.0.1/reply",
      "http://192.168.1.4/x",
      "https://box.local/x",
    ])
      expect(httpUrlHint(u)).toMatchObject({
        level: "warning",
        text: expect.stringContaining("cannot reach localhost"),
      });
  });
});
