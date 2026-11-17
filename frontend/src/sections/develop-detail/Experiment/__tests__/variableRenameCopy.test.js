// A prompt or agent run fills {{name}} only from the column named `name`.
// When a variable reads anything else, the words say what to do: rename the
// variable in the prompt (or the agent), or add the column. They never name
// the server's internals.
import { describe, expect, it } from "vitest";
import { renameVariableText } from "../serverBlockCopy";
import { serverExperimentBody } from "../serverBody";
import { EXPERIMENT_STEP } from "../constants";

const COLUMNS = [
  { id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51", name: "input" },
  { id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e52", name: "expected_output" },
];
const [INPUT] = COLUMNS;

const WALK_TEXT =
  "Experiments fill {{name}} only from a column named “name”. Rename {{name}} to {{input}} in the prompt, or add a column named “name” to the dataset.";

describe("renameVariableText", () => {
  it("a picked column: rename the variable to it, or add the column", () => {
    expect(renameVariableText("name", INPUT.id, COLUMNS, "prompt")).toBe(
      WALK_TEXT,
    );
  });

  it("the column of its name exists: pick it, or rename to the picked one", () => {
    const columns = [...COLUMNS, { id: "c-name", name: "name" }];
    expect(renameVariableText("name", INPUT.id, columns, "prompt")).toBe(
      "Experiments fill {{name}} only from a column named “name”. Pick “name” for {{name}}, or rename {{name}} to {{input}} in the prompt.",
    );
  });

  it.each([
    ["no column", ""],
    ["empty on every row", { kind: "empty" }],
    ["the same value on every row", { kind: "constant", value: "x" }],
    ["a nested path", `${INPUT.id}::$.text`],
  ])("%s: names the dataset's columns", (_label, value) => {
    expect(renameVariableText("name", value, COLUMNS)).toBe(
      "Experiments fill {{name}} only from a column named “name”. Rename {{name}} to a column name (input, expected_output) in the prompt, or add a column named “name” to the dataset.",
    );
  });

  it("names at most three columns", () => {
    const columns = ["a", "b", "c", "d"].map((n) => ({
      id: `c-${n}`,
      name: n,
    }));
    expect(renameVariableText("name", "", columns)).toContain(
      "Rename {{name}} to a column name (a, b, c, …) in the prompt,",
    );
  });

  it("a dotted name: mapping a path is not offered, only the rename", () => {
    const columns = [
      { id: "c-instruction", name: "instruction" },
      { id: "c-metadata", name: "metadata" },
    ];
    expect(
      renameVariableText(
        "metadata.customer.tier",
        "c-metadata::$.customer.tier",
        columns,
        "prompt",
      ),
    ).toBe(
      "Mapping {{metadata.customer.tier}} to a path inside a column isn't available in this release. Rename {{metadata.customer.tier}} to a column name (instruction, metadata) in the prompt.",
    );
  });

  it("no columns yet: still says what to do", () => {
    expect(renameVariableText("name", "", [])).toBe(
      "Experiments fill {{name}} only from a column named “name”. Rename {{name}} to a column name in the prompt, or add a column named “name” to the dataset.",
    );
  });

  it("an agent run says the agent", () => {
    expect(renameVariableText("name", INPUT.id, COLUMNS, "agent")).toBe(
      "Experiments fill {{name}} only from a column named “name”. Rename {{name}} to {{input}} in the agent, or add a column named “name” to the dataset.",
    );
  });
});

describe("the Data step block on the real backend", () => {
  const mappingBlock = (entry) =>
    serverExperimentBody(
      {
        name: "Exp",
        dataset_id: "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01",
        prompt_config: [entry],
        user_eval_metrics: [],
      },
      { columns: COLUMNS },
    ).blocks.find((b) => b.field === "prompt_config[].variable_mapping");

  it("a prompt run that reads another column blocks on Data with what to do", () => {
    const block = mappingBlock({
      target_type: "prompt",
      model: "test-model",
      prompt_id: "1b2c3d4e-0000-4000-8000-000000000001",
      prompt_version: "1b2c3d4e-0000-4000-8000-000000000002",
      variable_mapping: { name: INPUT.id },
    });
    expect(block).toEqual({
      field: "prompt_config[].variable_mapping",
      step: EXPERIMENT_STEP.DATA,
      text: WALK_TEXT,
      generic: false,
    });
    expect(block.text).not.toMatch(/template path|isn't supported/);
  });

  it("an agent run that reads another column says the agent", () => {
    const block = mappingBlock({
      target_type: "agent",
      agent_id: "1b2c3d4e-0000-4000-8000-000000000003",
      agent_version: "1b2c3d4e-0000-4000-8000-000000000004",
      variable_mapping: { name: INPUT.id },
    });
    expect(block?.text).toBe(
      "Experiments fill {{name}} only from a column named “name”. Rename {{name}} to {{input}} in the agent, or add a column named “name” to the dataset.",
    );
  });
});
