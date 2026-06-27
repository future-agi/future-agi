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

  it("derives evalType from tags when eval_type is missing", () => {
    expect(
      normalizeOldEndpointEval({
        id: "e",
        name: "n",
        eval_template_tags: ["CODE_EVAL"],
      }).evalType,
    ).toBe("code");
    expect(
      normalizeOldEndpointEval({
        id: "e",
        name: "n",
        eval_template_tags: ["AGENT_EVAL"],
      }).evalType,
    ).toBe("agent");
    expect(
      normalizeOldEndpointEval({ id: "e", name: "n" }).evalType,
    ).toBe("llm");
  });
});
