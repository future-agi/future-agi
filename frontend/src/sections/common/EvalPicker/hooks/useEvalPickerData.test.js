import { describe, expect, it } from "vitest";

import { normalizeOldEndpointEval } from "./useEvalPickerData";

describe("normalizeOldEndpointEval", () => {
  // The bug TH-6125 closed: the mapper used to rename `created_by_name` to
  // `createdByName` (camelCase). EvalPickerList reads `created_by_name`
  // (snake_case, the backend's contract) so the rename silently produced
  // `undefined` → fell back to "Unknown" on every row. The mapping is
  // FE-internal so no API contract can guard it — this test is the only
  // regression lock.
  it("preserves created_by_name as snake_case (TH-6125 regression)", () => {
    const out = normalizeOldEndpointEval({
      id: "e1",
      name: "my eval",
      created_by_name: "Aman Sharma",
    });
    expect(out.created_by_name).toBe("Aman Sharma");
    expect(out).not.toHaveProperty("createdByName");
  });

  it("falls back to System when owner is system and the field is missing", () => {
    const out = normalizeOldEndpointEval({
      id: "e1",
      name: "preset",
      type: "futureagi_built",
    });
    expect(out.created_by_name).toBe("System");
    expect(out.owner).toBe("system");
  });

  it("falls back to User when owner is user and the field is missing", () => {
    const out = normalizeOldEndpointEval({
      id: "e1",
      name: "custom",
      owner: "user",
    });
    expect(out.created_by_name).toBe("User");
  });

  it("uses template_id as the canonical id; sets userEvalId only on attached rows", () => {
    // Catalog row — no template_id; id IS the template id.
    const catalog = normalizeOldEndpointEval({
      id: "tpl-1",
      name: "preset",
      type: "futureagi_built",
    });
    expect(catalog.id).toBe("tpl-1");
    expect(catalog.templateId).toBe("tpl-1");
    expect(catalog.userEvalId).toBeUndefined();

    // Attached UserEvalMetric row — separate template_id and id.
    const attached = normalizeOldEndpointEval({
      id: "uem-1",
      template_id: "tpl-1",
      name: "my eval",
    });
    expect(attached.id).toBe("tpl-1");
    expect(attached.templateId).toBe("tpl-1");
    expect(attached.userEvalId).toBe("uem-1");
  });

  it("derives eval_type from tags when eval_type is missing", () => {
    expect(
      normalizeOldEndpointEval({
        id: "e",
        name: "n",
        eval_template_tags: ["CODE_EVAL"],
      }).eval_type,
    ).toBe("code");
    expect(
      normalizeOldEndpointEval({
        id: "e",
        name: "n",
        eval_template_tags: ["AGENT_EVAL"],
      }).eval_type,
    ).toBe("agent");
    expect(
      normalizeOldEndpointEval({ id: "e", name: "n" }).eval_type,
    ).toBe("llm");
  });

  it("emits snake_case keys for template_type / output_type / last_updated / current_version", () => {
    const out = normalizeOldEndpointEval({
      id: "e1",
      name: "my eval",
      template_type: "composite",
      output_type: "score",
      updated_at: "2026-06-29T10:00:00Z",
      current_version: "v2",
      eval_template_tags: ["AGENT_EVAL"],
    });
    expect(out.template_type).toBe("composite");
    expect(out.output_type).toBe("score");
    expect(out.last_updated).toBe("2026-06-29T10:00:00Z");
    expect(out.current_version).toBe("v2");
    expect(out.eval_template_tags).toEqual(["AGENT_EVAL"]);
    expect(out).not.toHaveProperty("templateType");
    expect(out).not.toHaveProperty("outputType");
    expect(out).not.toHaveProperty("lastUpdated");
    expect(out).not.toHaveProperty("currentVersion");
    expect(out).not.toHaveProperty("evalTemplateTags");
  });

  // Realistic getEvalsList response shape captured from the wire. Asserts the
  // mapper produces a row that lines up with the typed eval-templates/list
  // contract (EvalTemplateListItemSerializer) so EvalPickerList renders both
  // endpoints' rows through the same snake_case reads.
  const OLD_ENDPOINT_FIXTURE = {
    id: "uem-42",
    template_id: "tpl-7",
    name: "customer_agent_clarification_seeking",
    template_type: "single",
    eval_type: "agent",
    output_type: "pass_fail",
    owner: "user",
    created_by_name: "Karthik Avinash",
    current_version: "v3",
    updated_at: "2026-06-28T12:34:56Z",
    is_draft: false,
    eval_required_keys: ["conversation"],
    eval_template_tags: ["AGENT_EVAL"],
    description: "Checks whether the agent asks for clarification.",
    model: "gpt-4o-mini",
  };

  it("maps a realistic old-endpoint row to the snake_case picker shape", () => {
    const out = normalizeOldEndpointEval(OLD_ENDPOINT_FIXTURE);
    expect(out).toMatchObject({
      id: "tpl-7",
      templateId: "tpl-7",
      userEvalId: "uem-42",
      name: "customer_agent_clarification_seeking",
      template_type: "single",
      eval_type: "agent",
      output_type: "pass_fail",
      owner: "user",
      created_by_name: "Karthik Avinash",
      current_version: "v3",
      last_updated: "2026-06-28T12:34:56Z",
      is_draft: false,
      required_keys: ["conversation"],
      eval_template_tags: ["AGENT_EVAL"],
      description: "Checks whether the agent asks for clarification.",
      model: "gpt-4o-mini",
    });
    for (const camel of [
      "templateType",
      "evalType",
      "outputType",
      "lastUpdated",
      "currentVersion",
      "isDraft",
      "requiredKeys",
      "evalTemplateTags",
    ]) {
      expect(out).not.toHaveProperty(camel);
    }
  });
});
