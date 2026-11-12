// HO D9: apply an eval group (keys, auto-mapping, the body, refusals) and
// the New experiment add that carries the picker's chips and version.
import { describe, expect, it } from "vitest";
import {
  APPLY_PAGE,
  applyBlocked,
  applyBody,
  appliedText,
  autoMapping,
  groupKeys,
  pickerAddOf,
} from "../applyGroupModel";

const MEMBERS = [
  {
    eval_template_id: "a",
    name: "Correctness",
    required_keys: ["output", "expected"],
    optional_keys: ["input"],
  },
  {
    eval_template_id: "b",
    name: "Address updated",
    required_keys: ["output"],
    optional_keys: [],
  },
  {
    eval_template_id: "c",
    name: "Citation accuracy",
    required_keys: ["output", "context"],
    optional_keys: ["expected"],
  },
];
const COLUMNS = [
  { id: "c1", name: "customer_message", role: "input" },
  { id: "c3", name: "expected_reply", role: "expected" },
  { id: "c4", name: "expected_tool_calls", role: "expected" },
  { id: "c10", name: "Draft reply", role: "output" },
  { id: "c9", name: "policy_kb", role: null },
];

describe("HO D9 · apply a group", () => {
  it("collects the keys of the selected members (required wins)", () => {
    expect(groupKeys(MEMBERS)).toEqual({
      required: ["output", "expected", "context"],
      optional: ["input"],
    });
    expect(groupKeys(MEMBERS, ["a", "c"])).toEqual({
      required: ["output"],
      optional: [],
    });
  });

  it("maps keys the way the dataset's saved evals do, else by the one column of the role", () => {
    const saved = [
      { mapping: { output: "c10", context: "policy_kb" } },
      { mapping: { expected: "expected_reply" } },
    ];
    expect(
      autoMapping(["output", "expected", "context", "input"], COLUMNS, saved),
    ).toEqual({ output: "c10", expected: "c3", context: "c9", input: "c1" });
    // Two expected-role columns and no saved mapping: left for the user.
    expect(autoMapping(["expected"], COLUMNS, [])).toEqual({});
  });

  it("says why Apply is off", () => {
    expect(
      applyBlocked({ members: MEMBERS, deselected: ["a", "b", "c"] }),
    ).toBe("Select at least one evaluation in the group to apply.");
    expect(
      applyBlocked({
        members: MEMBERS,
        deselected: [],
        mapping: { output: "c10" },
      }),
    ).toBe("Map expected, context to columns.");
    expect(
      applyBlocked({
        members: MEMBERS,
        deselected: ["a", "c"],
        mapping: { output: "c10" },
      }),
    ).toBeNull();
  });

  it("builds the DATASET and EXPERIMENT bodies", () => {
    expect(APPLY_PAGE).toEqual({
      dataset: "DATASET",
      experiment: "EXPERIMENT",
    });
    expect(
      applyBody({
        groupId: "g",
        page: "dataset",
        datasetId: "d",
        mapping: { output: "c10", input: "" },
        deselected: ["a"],
      }),
    ).toEqual({
      eval_group_id: "g",
      page_id: "DATASET",
      filters: { dataset_id: "d" },
      mapping: { output: "c10" },
      deselected_evals: ["a"],
    });
    expect(
      applyBody({ groupId: "g", page: "experiment", experimentId: "e" })
        .filters,
    ).toEqual({ experiment_id: "e" });
    expect(appliedText("Reply quality", 2, "dataset")).toBe(
      "Added 2 evaluations from Reply quality to this dataset · Run them to score the rows",
    );
    expect(appliedText("Reply quality", 1, "experiment")).toBe(
      "Added 1 evaluation from Reply quality · scored on this experiment's runs",
    );
  });

  it("adds a member as the EvalPicker does: output type, keys, the version pinned", () => {
    const tpl = {
      id: "t1",
      name: "Tone",
      output_type: "Pass/Fail",
      eval_type: "llm",
      template_type: "single",
      model: "gpt-4o",
      required_keys: ["output"],
      config: { required_keys: ["output"] },
    };
    const versions = [
      { id: "v4", version_number: 4 },
      { id: "v3", version_number: 3, is_default: true },
    ];
    const add = pickerAddOf(tpl, versions);
    expect(add).toMatchObject({
      templateId: "t1",
      evalTemplateId: "t1",
      name: "Tone",
      evalTemplate: tpl,
      outputType: "Pass/Fail",
      requiredKeys: ["output"],
      versionPin: "v3",
      versionPinLabel: "V3 · pinned",
      versionId: "v3",
      mapping: {},
    });
    expect(add.config.version_pin).toBe("v3");
    // No versions: nothing pinned.
    expect(pickerAddOf(tpl, []).versionPinLabel).toBeUndefined();
  });
});
