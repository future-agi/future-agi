import { describe, expect, it } from "vitest";

import { CATALOG, catalogEntry } from "../index";
import {
  formToConfig,
  formValuesFor,
  normalizeGuardrails,
  ruleBodyForSave,
  ruleRows,
  storedActionOf,
} from "../rule-model";
import { fixedScoreNote } from "../stored-policy";

// MCP Security checks model requests, not /mcp endpoint calls. MCP Security
// and Tool Permissions score 1.0 on a match and 0 otherwise, and the gateway
// triggers a check whose score is above its threshold (guardrail.go
// shouldTrigger): any threshold below 1 runs them the same way, and 1 or
// more stops them.

describe("a fixed-score check's stored threshold", () => {
  const tools = catalogEntry("tool-permissions");
  const mcp = catalogEntry("mcp-security");

  it.each([0, 0.05, 0.5, 0.8, 0.95, 0.999])(
    "says nothing of %s, which runs the check as any threshold below 1 does",
    (threshold) => {
      expect(fixedScoreNote(tools, threshold)).toBeNull();
      expect(fixedScoreNote(mcp, threshold)).toBeNull();
    },
  );

  it.each([
    [1, "1.00"],
    [1.5, "1.50"],
  ])("says %s stops the check", (threshold, shown) => {
    expect(fixedScoreNote(tools, threshold)).toBe(
      `Stored match threshold ${shown}: this check scores 1.0 on a match, so at ${shown} it never triggers.`,
    );
  });

  it("says nothing for a check whose score is graded", () => {
    expect(fixedScoreNote(catalogEntry("pii-detection"), 1)).toBeNull();
    expect(fixedScoreNote(catalogEntry("lakera-guard"), 1)).toBeNull();
  });

  it("hides the slider of both fixed-score checks only", () => {
    expect(tools.hideConfidence).toBe(true);
    expect(mcp.hideConfidence).toBe(true);
    expect(
      CATALOG.filter((entry) => entry.fixedScore).map((entry) => entry.name),
    ).toEqual(["tool-permissions", "mcp-security"]);
  });
});

// Allowed Servers is not offered on any form (GL-1029-2: removed as in the
// base, noToolOrMcpChecks.test.jsx), and a stored value is never deleted:
// every save keeps it exactly as stored.
describe("Allowed Servers, which the gateway does not read", () => {
  const mcp = catalogEntry("mcp-security");
  const stored = {
    allowed_servers: " walk-echo ",
    blocked_tools: "db_write",
    validate_inputs: "true",
    max_calls_per_request: 4,
    provider: "mcp_security",
  };

  it("is not a form field, and is kept exactly as stored whatever the form holds", () => {
    expect(mcp.fields.map((field) => field.key)).not.toContain(
      "allowed_servers",
    );
    const form = formValuesFor(mcp, { config: stored }, (c) =>
      storedActionOf(c),
    );
    expect(form.fields).not.toHaveProperty("allowed_servers");
    const config = formToConfig(
      mcp,
      { ...form.fields, allowed_servers: "something else" },
      stored,
    );
    expect(JSON.stringify(config)).toBe(JSON.stringify(stored));
  });

  it("is never written for a check that does not store it", () => {
    const config = formToConfig(
      mcp,
      { allowed_servers: "github", blocked_tools: "shell" },
      {},
    );
    expect(config).not.toHaveProperty("allowed_servers");
    expect(config).toEqual({
      blocked_tools: "shell",
      provider: "mcp_security",
    });
  });

  it("stays in a rule saved with another field edited", () => {
    const saved = {
      rules: [
        {
          name: "mcp-security",
          stage: "pre",
          action: "warn",
          threshold: 0.5,
          enabled: true,
          config: stored,
        },
      ],
    };
    const check = normalizeGuardrails(saved).checks["mcp-security"];
    const form = formValuesFor(mcp, check, (c) => storedActionOf(c));
    const body = ruleBodyForSave({
      saved,
      check,
      meta: mcp,
      catalogName: "mcp-security",
      form: { ...form, fields: { ...form.fields, blocked_tools: "shell" } },
    });
    expect(JSON.stringify(body.config.config)).toBe(
      JSON.stringify({ ...stored, blocked_tools: "shell" }),
    );
    expect(body.config).toMatchObject({
      action: "warn",
      threshold: 0.5,
      enabled: true,
    });
  });
});

describe("the rules grid's rows", () => {
  const saved = {
    rules: [
      { name: "mcp-security", enabled: true, threshold: 0.5, config: {} },
      { name: "tool-permissions", enabled: true, threshold: 1, config: {} },
      // A graded check (PII Detection scores 1.0 on a match in the base
      // catalog, scoresOneOnMatch, so no threshold is shown for it).
      { name: "injection-detector", enabled: true, threshold: 1, config: {} },
    ],
  };
  const byId = Object.fromEntries(
    ruleRows(normalizeGuardrails(saved)).map((row) => [row.id, row]),
  );

  it("names a stored threshold that stops a fixed-score check, and no other", () => {
    expect(byId["tool-permissions"].threshold).toBeNull();
    expect(byId["tool-permissions"].thresholdNote).toMatch(/never triggers/);
    expect(byId["mcp-security"].thresholdNote).toBeNull();
    expect(byId["prompt-injection"].threshold).toBe(1);
    expect(byId["prompt-injection"].thresholdNote).toBeNull();
  });
});
