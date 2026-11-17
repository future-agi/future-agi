import { describe, expect, it } from "vitest";

import { CATALOG, STAGE_LABELS, catalogEntry, checkStage } from "../../catalog";
import {
  actionOf,
  mapEditForSave,
  normalizeGuardrails,
  ruleBodyForSave,
  ruleRows,
} from "../../catalog/rule-model";
import { formValuesFor } from "../../catalog/form-model";
import { ruleColumns } from "../rule-columns";

// Where a rule runs is its check type's stage, and only that: the gateway runs
// an organization's check at its type's stage (guardrails/plugin.go
// eachOrgCheck: g.Stage() == stage), and a stage stored with the rule never
// reaches it. So the rules list shows the type's stage, no form offers one to
// pick, and a stored "stage" moves neither a row nor what a save sends.
describe("Rules: Runs on is the check type's stage", () => {
  const stageColumn = () =>
    ruleColumns({ onToggle: () => {}, onSetUp: () => {} }).find(
      (column) => column.field === "stage",
    );

  it("labels each row with its type's stage, whatever stage was stored", () => {
    const rows = ruleRows({
      checks: {
        "pii-detection": { enabled: true, stage: "response" },
        "hallucination-detection": { enabled: true, stage: "request" },
        "data-leakage-prevention": { enabled: true, phase: "pre" },
      },
    });
    const stage = Object.fromEntries(rows.map((row) => [row.id, row.stage]));
    expect(stage).toEqual({
      "pii-detection": "request",
      "hallucination-detection": "response",
      "data-leakage-prevention": "response",
    });
    const format = stageColumn().valueFormatter;
    expect(format({ value: stage["pii-detection"] })).toBe("Before LLM");
    expect(format({ value: stage["hallucination-detection"] })).toBe(
      "After LLM",
    );
  });

  it("reads the model's answer only for the types that do", () => {
    const answerTypes = CATALOG.filter(
      (entry) => checkStage(entry.name) === "response",
    ).map((entry) => entry.name);
    expect(answerTypes.sort()).toEqual([
      "data-leakage-prevention",
      "hallucination-detection",
    ]);
    expect(Object.keys(STAGE_LABELS).sort()).toEqual(["request", "response"]);
  });

  it("offers no stage to pick in any check's settings", () => {
    for (const entry of CATALOG) {
      for (const field of entry.fields || []) {
        expect(field.key, `${entry.name}.${field.key}`).not.toMatch(
          /^(stage|phase)$/,
        );
        expect(String(field.label || "")).not.toMatch(/stage|runs on|phase/i);
      }
    }
  });

  it("adds no stage to a save; a stored one is sent as stored", () => {
    const pii = catalogEntry("pii-detection");
    const answer = catalogEntry("hallucination-detection");
    // A new answer-type rule: After LLM in the column, and no stage sent.
    const fresh = ruleBodyForSave({
      saved: { rules: [] },
      check: null,
      meta: answer,
      catalogName: answer.name,
      form: formValuesFor(answer, null, actionOf),
    });
    expect(JSON.stringify(fresh)).not.toMatch(/"(stage|phase)"/);

    // A rules-list entry with a legacy stage that disagrees with its type:
    // the row stays Before LLM and a re-save sends the stored value as stored.
    const rule = {
      name: "pii-detection",
      mode: "sync",
      enabled: true,
      action: "block",
      threshold: 0.8,
      stage: "response",
      config: {},
    };
    const saved = { rules: [rule] };
    const check = normalizeGuardrails(saved).checks["pii-detection"];
    expect(ruleRows(normalizeGuardrails(saved))[0].stage).toBe("request");
    const form = { ...formValuesFor(pii, check, actionOf), enabled: false };
    const body = ruleBodyForSave({
      saved,
      check,
      meta: pii,
      catalogName: pii.name,
      form,
    });
    expect(body.config.stage).toBe("response");
    expect(body.config.enabled).toBe(false);

    // A checks-list section and a checks map send only what the form changed.
    const listed = { checks: [{ ...rule }] };
    const listedCheck = normalizeGuardrails(listed).checks["pii-detection"];
    const edit = ruleBodyForSave({
      saved: listed,
      check: listedCheck,
      meta: pii,
      catalogName: pii.name,
      form: { ...formValuesFor(pii, listedCheck, actionOf), enabled: false },
    });
    expect(Object.keys(edit.config)).toEqual(["enabled"]);
    const mapped = mapEditForSave({
      check: { enabled: true, action: "block", stage: "response" },
      meta: pii,
      catalogName: pii.name,
      form: { ...form, enabled: false },
    });
    expect(JSON.stringify(mapped)).not.toMatch(/"(stage|phase)"/);
  });
});
