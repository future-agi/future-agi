import { describe, expect, it } from "vitest";
import { runTestEventFields } from "../common";

const MARK = "SYNTHETIC_EVAL_SETTING_7d10";

describe("runTestEventFields", () => {
  it("sends the agent version and counts, not the test text or evaluation settings", () => {
    const payload = {
      name: "Synthetic test name",
      description: "Synthetic test description",
      scenario_ids: ["s-1", "s-2", "s-3"],
      agent_definition_id: "agent-1",
      agent_version: "version-2",
      eval_config_ids: [],
      evaluations_config: [
        {
          name: "Synthetic eval",
          template_id: "tpl-1",
          mapping: { input: "transcript" },
          config: {
            params: { url: `https://api.example.test/${MARK}` },
            headers: { Authorization: MARK },
          },
          error_localizer: false,
        },
      ],
      dataset_row_ids: [],
      enable_tool_evaluation: true,
    };

    const fields = runTestEventFields(payload);

    expect(fields).toEqual({
      agent_definition_id: "agent-1",
      agent_version: "version-2",
      scenario_count: 3,
      evaluation_count: 1,
      enable_tool_evaluation: true,
    });
    expect(JSON.stringify(fields)).not.toContain(MARK);
    expect(JSON.stringify(fields)).not.toContain("Synthetic");
  });

  it("counts missing lists as zero", () => {
    expect(runTestEventFields({ agent_definition_id: "agent-1" })).toEqual({
      agent_definition_id: "agent-1",
      agent_version: undefined,
      scenario_count: 0,
      evaluation_count: 0,
      enable_tool_evaluation: false,
    });
  });
});
