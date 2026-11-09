// L7c g7: wizard card chips (wiz:card-chips), the card Test body
// (test:card) and the reference column a code check names
// (wiz:add-evaluation); the Create form's name refusal (evalcreate:name).
import { describe, expect, it, vi } from "vitest";

// This original whole-definition fixture includes the prototype-only N/A
// contract. evalDevParity.test.js separately covers the real backend body.
vi.mock("src/utils/prototype", () => ({ isPrototypeMode: () => true }));
import {
  cardChipsOf,
  cardTestBodyOf,
  refusalTextOf,
  namedReferenceColumnOf,
} from "./evalCardModel-g7";
import { createNameErrorOf } from "src/sections/evals/components/createNameError";

describe("cardChipsOf", () => {
  it("a pass/fail eval shows no bar; its judge shows alone", () => {
    const c = cardChipsOf({
      outputType: "pass_fail",
      passThreshold: 0.5,
      model: "gpt-4o",
    });
    expect(c.passAt).toBeNull();
    expect(c.judge).toBe("gpt-4o");
  });
  it("a card added from the picker reads its template's type, bar and version", () => {
    const c = cardChipsOf(
      { model: "turing_large" },
      { output_type: "percentage", pass_threshold: 0.7, current_version: "V3" },
    );
    expect(c).toMatchObject({
      outputType: "percentage",
      passAt: 0.7,
      version: "v3",
    });
  });
  it("the card's own fields win", () => {
    const c = cardChipsOf(
      { outputType: "score", passThreshold: 0.9, version: "V2 · pinned" },
      { output_type: "percentage", pass_threshold: 0.7, current_version: "V3" },
    );
    expect(c).toMatchObject({ passAt: 0.9, version: "V2 · pinned" });
  });
});

describe("cardTestBodyOf", () => {
  it("sends the card's whole definition", () => {
    const body = cardTestBodyOf({
      templateId: "t1",
      model: "gpt-4o",
      mapping: { output: "hi" },
      threshold: 0.9,
      outputType: "score",
      datasetId: "d1",
      rowId: "r1",
      output: "hi",
      instructions: "Pass if it names a voucher",
      card: {
        errorLocalizer: true,
        config: {
          allow_na: true,
          few_shot_examples: [{ dataset_id: "x" }],
          run_config: { agent_mode: "agent" },
        },
      },
    });
    expect(body.error_localizer).toBe(true);
    expect(body.config).toMatchObject({
      threshold: 0.9,
      output_type: "score",
      rule_prompt: "Pass if it names a voucher",
      allow_na: true,
      few_shot_examples: [{ dataset_id: "x" }],
      run_config: { agent_mode: "agent" },
    });
    expect(body.row_context).toMatchObject({
      source: "experiment_create",
      row_id: "r1",
    });
  });
});

describe("namedReferenceColumnOf", () => {
  const cols = [
    { id: "c1", name: "expected_reply" },
    { id: "c2", name: "expected_tool_calls" },
  ];
  it("finds the reference column a description names", () => {
    expect(
      namedReferenceColumnOf(
        "Match between the tool calls made and expected_tool_calls (names and arguments, 0–1).",
        cols,
      )?.id,
    ).toBe("c2");
    expect(
      namedReferenceColumnOf("Does the reply answer the question?", cols),
    ).toBeNull();
  });
});

describe("createNameErrorOf", () => {
  it("reads a duplicate-name refusal and ignores other failures", () => {
    expect(
      createNameErrorOf({
        status: false,
        result: "An evaluation named refund_check already exists.",
        statusCode: 400,
      }),
    ).toBe("An evaluation named refund_check already exists.");
    expect(
      createNameErrorOf({ result: "Instructions are required" }),
    ).toBeNull();
    expect(createNameErrorOf(new Error("Network Error"))).toBeNull();
  });
});

describe("refusalTextOf (byok:settings-page)", () => {
  it("drops the machine code, keeps the words", () => {
    expect(
      refusalTextOf(
        "provider_not_configured: Add your OpenAI key in Settings > AI Providers to judge with gpt-4o.",
        "provider_not_configured",
      ),
    ).toBe(
      "Add your OpenAI key in Settings > AI Providers to judge with gpt-4o.",
    );
    expect(refusalTextOf("The test did not run", null)).toBe(
      "The test did not run",
    );
  });
});
