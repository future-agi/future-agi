import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import { catalogEntry } from "../../guardrails/catalog";
import {
  formValuesFor,
  mapEditForSave,
  normalizeGuardrails,
  ruleBodyForSave,
  ruleRows,
  storedActionOf,
} from "../../guardrails/catalog/rule-model";
import {
  buildConfigPatch,
  guardrailsForSave,
  isStoredChecksMap,
} from "../../hooks/useConfigPatch";
import GuardrailConfigTab from "../GuardrailConfigTab";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));
// The Org config editor's switches, Configure and Remove are Owner or Admin
// only (GatewayWriteControls, useGatewayAdmin): these cases edit as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

// A guardrails section is stored in one of three shapes, and the config push
// reads each one's threshold its own way (agentcc/services/config_push.py
// _transform_guardrails, then the contract's dump with exclude_none):
// - a rule, or an entry of the policy sync's checks list: its `threshold`,
//   0.8 when it has none, and none at all when it is saved as null, which the
//   gateway runs at 0; no other spelling is read;
// - a check of a checks map, forwarded as stored: the contract reads its
//   confidence_threshold key when it is saved, even as null, else
//   confidenceThreshold; a null or absent one runs at 0.
// The Rules grid, the rule drawer, the Org config card and its dialog show
// what the gateway runs, and saving any of them unchanged changes nothing.
// Prompt Injection is a graded check, so every surface shows its threshold
// (PII Detection scores 1.0 on a match in the base catalog, scoresOneOnMatch,
// and shows none).

const sections = {
  rules: (fields) => ({
    rules: [{ name: "injection-detector", enabled: true, ...fields }],
  }),
  "checks-list": (fields) => ({
    checks: [
      {
        name: "injection-detector",
        enabled: true,
        ...fields,
        _policy: "policy-a",
      },
    ],
  }),
  "checks-map": (fields) => ({
    checks: { "prompt-injection": { enabled: true, ...fields } },
  }),
};

// [shape, the stored threshold keys, what the gateway runs the check at]
const THRESHOLDS = [
  ["rules", { threshold: null }, 0],
  ["rules", {}, 0.8],
  ["rules", { threshold: 0 }, 0],
  ["rules", { threshold: 0.35 }, 0.35],
  ["rules", { confidence_threshold: 0.35 }, 0.8],
  ["checks-list", { threshold: null }, 0],
  ["checks-list", {}, 0.8],
  ["checks-list", { threshold: 0 }, 0],
  ["checks-list", { confidence_threshold: 0.35, threshold: 0.9 }, 0.9],
  ["checks-list", { confidence_threshold: 0.35 }, 0.8],
  ["checks-list", { confidenceThreshold: 0.35 }, 0.8],
  ["checks-map", { confidence_threshold: null }, 0],
  ["checks-map", {}, 0],
  ["checks-map", { confidence_threshold: null, confidenceThreshold: 0.35 }, 0],
  ["checks-map", { confidenceThreshold: 0.35 }, 0.35],
  ["checks-map", { confidence_threshold: 0.2, confidenceThreshold: 0.35 }, 0.2],
];

const clone = (value) => JSON.parse(JSON.stringify(value));

function renderOrgConfig(saved) {
  const change = vi.fn();
  render(
    <GuardrailConfigTab
      guardrails={saved}
      saved={saved}
      onChange={change}
      nodePipeline={{ enabled: true, mode: "sequential", fail_open: false }}
    />,
  );
  return change;
}

const cardFor = (label) =>
  screen
    .getAllByText(label)
    .map((node) => node.closest(".MuiCard-root"))
    .find(Boolean);

async function openDialog(label) {
  fireEvent.click(within(cardFor(label)).getAllByRole("button")[0]);
  return screen.findByRole("dialog");
}

function savedPatch(saved, draft) {
  return buildConfigPatch(
    { guardrails: saved },
    { guardrails: guardrailsForSave(draft, saved) },
    ["guardrails"],
  );
}

// The rule drawer as RulesView opens it (RuleDrawer's formValuesFor) and
// saves it (useRuleActions.saveRule).
function openRow(saved, id) {
  const asMap = isStoredChecksMap(saved);
  const row = ruleRows(normalizeGuardrails(saved), { asMap }).find(
    (r) => r.id === id,
  );
  const meta = catalogEntry(id);
  const values = formValuesFor(
    meta,
    row.check,
    (c) => storedActionOf(c, { asMap }),
    asMap ? 0 : 0.8,
  );
  return { asMap, row, meta, values };
}

function drawerSave(saved, id, edit = (values) => values) {
  const { asMap, row, meta, values } = openRow(saved, id);
  const form = edit(values);
  const args = { check: row.check, meta, catalogName: id, form };
  return asMap ? mapEditForSave(args) : ruleBodyForSave({ ...args, saved });
}

// What update-guardrail stores for an entry of a checks list no policy owns
// (guardrail_policy_checks.edit_in_checks_list, _edited_fields and
// _put_in_list): the sent fields over the stored entry, never its name or a
// `_` key, a threshold sent as confidence_threshold kept as threshold.
function storedAfterListEdit(entry, sent) {
  const fields = Object.fromEntries(
    Object.entries(sent).filter(
      ([key]) => !key.startsWith("_") && key !== "name",
    ),
  );
  ["confidence_threshold", "confidenceThreshold"].forEach((alias) => {
    if (alias in fields) {
      const value = fields[alias];
      delete fields[alias];
      if (!("threshold" in fields)) fields.threshold = value;
    }
  });
  return { ...entry, ...fields, name: entry.name };
}

describe.each(THRESHOLDS)(
  "a %s check stored with %j",
  (shape, fields, runs) => {
    it(`shows ${runs.toFixed(2)}, the threshold the gateway runs, everywhere`, async () => {
      const saved = sections[shape](fields);
      const { row, values } = openRow(saved, "prompt-injection");
      expect(row.threshold).toBe(runs);
      expect(values.threshold).toBe(runs);

      renderOrgConfig(saved);
      expect(
        within(cardFor("Prompt Injection")).getByText(
          `Threshold: ${runs.toFixed(2)}`,
        ),
      ).toBeInTheDocument();
      const dialog = await openDialog("Prompt Injection");
      expect(
        within(dialog).getByText(`Confidence Threshold: ${runs.toFixed(2)}`),
      ).toBeInTheDocument();
    });

    it("is saved unchanged by an unchanged drawer or dialog save", async () => {
      const saved = sections[shape](fields);
      const bytes = JSON.stringify(saved);
      const body = drawerSave(saved, "prompt-injection");
      if (shape === "rules") {
        expect(JSON.stringify(body)).toBe(
          JSON.stringify({
            name: "injection-detector",
            config: saved.rules[0],
          }),
        );
      } else {
        expect(body).toBeNull();
      }

      const change = renderOrgConfig(saved);
      const dialog = await openDialog("Prompt Injection");
      fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
      expect(savedPatch(saved, change.mock.calls[0][0])).toEqual({});
      expect(JSON.stringify(saved)).toBe(bytes);
    });
  },
);

// MCP Security scores 1.0 on a match, so a threshold of 1 or more never
// triggers it; the rules grid's row says so exactly when the threshold the
// gateway runs is one. (The Org config editor offers no MCP Security card or
// dialog: noToolOrMcpChecks.test.jsx.)
describe("the never-triggers warning reads what the push sends", () => {
  const mcp = (shape, fields) =>
    shape === "checks-map"
      ? { checks: { "mcp-security": { enabled: true, ...fields } } }
      : {
          [shape === "rules" ? "rules" : "checks"]: [
            { name: "mcp-security", enabled: true, ...fields },
          ],
        };

  it.each([
    ["checks-list", { confidence_threshold: 0.5, threshold: 1 }, true],
    ["checks-list", { threshold: 1 }, true],
    ["checks-list", { confidence_threshold: 1 }, false],
    ["checks-list", { confidenceThreshold: 1 }, false],
    ["checks-list", { threshold: null, confidence_threshold: 1 }, false],
    ["rules", { confidence_threshold: 1 }, false],
    ["rules", { threshold: null }, false],
    ["rules", { threshold: 1 }, true],
    [
      "checks-map",
      { confidence_threshold: null, confidenceThreshold: 1 },
      false,
    ],
    ["checks-map", { confidenceThreshold: 1 }, true],
    ["checks-map", { confidence_threshold: 1, confidenceThreshold: 0.5 }, true],
  ])("%s %j warns: %s", (shape, fields, warns) => {
    const saved = mcp(shape, fields);
    const [row] = ruleRows(normalizeGuardrails(saved), {
      asMap: shape === "checks-map",
    });
    expect(Boolean(row.thresholdNote?.includes("never triggers"))).toBe(warns);
  });
});

// A drawer save of a check of the policy sync's checks list sends
// update-guardrail only what the admin changed: update-guardrail merges it
// over the stored entry, so every untouched field stays exactly as stored.
describe("a drawer save of a checks-list entry", () => {
  const MCP_ENTRY = {
    name: "mcp-security",
    enabled: false,
    threshold: null,
    future_rule_key: " untouched ",
    config: {
      validate_inputs: " true ",
      blocked_tools: " db_write ",
      custom_patterns: ["LIST_MARKER"],
      future_setting: { keep: [null, " exact "] },
    },
    _policy: "policy-mcp",
  };
  const listOf = (...entries) => ({ checks: clone(entries) });

  it("re-enables by sending only enabled, keeping every stored field", () => {
    const saved = listOf(MCP_ENTRY);
    const body = drawerSave(saved, "mcp-security", (v) => ({
      ...v,
      enabled: true,
    }));
    expect(body).toEqual({ name: "mcp-security", config: { enabled: true } });
    const stored = storedAfterListEdit(saved.checks[0], body.config);
    expect(stored).toEqual({ ...MCP_ENTRY, enabled: true });
    expect(JSON.stringify(stored.config)).toBe(
      JSON.stringify(MCP_ENTRY.config),
    );
    expect(stored).not.toHaveProperty("mode");
    expect(stored).not.toHaveProperty("action");
    expect(stored.config).not.toHaveProperty("provider");
    expect(stored.config).not.toHaveProperty("max_calls_per_request");
  });

  it("sends nothing when nothing changed", () => {
    expect(drawerSave(listOf(MCP_ENTRY), "mcp-security")).toBeNull();
  });

  it("sends the whole stored config with only the edited field changed", () => {
    const body = drawerSave(listOf(MCP_ENTRY), "mcp-security", (v) => ({
      ...v,
      fields: { ...v.fields, blocked_tools: " lookup " },
    }));
    expect(body).toEqual({
      name: "mcp-security",
      config: { config: { ...MCP_ENTRY.config, blocked_tools: "lookup" } },
    });
  });

  it("sends an edited threshold as threshold, the key the push reads", () => {
    const entry = {
      name: "pii-detector",
      enabled: true,
      confidence_threshold: 0.35,
      threshold: 0.9,
    };
    const body = drawerSave(listOf(entry), "pii-detection", (v) => ({
      ...v,
      threshold: 0.5,
    }));
    expect(body).toEqual({ name: "pii-detector", config: { threshold: 0.5 } });
    expect(storedAfterListEdit(entry, body.config)).toEqual({
      ...entry,
      threshold: 0.5,
    });
  });

  it("still initializes a check the list does not hold yet", () => {
    const meta = catalogEntry("mcp-security");
    const form = formValuesFor(meta, null, storedActionOf);
    form.fields.blocked_tools = " lookup ";
    const body = ruleBodyForSave({
      saved: listOf({ name: "pii-detector", enabled: true }),
      check: null,
      meta,
      catalogName: "mcp-security",
      form,
    });
    expect(body).toEqual({
      name: "mcp-security",
      config: {
        name: "mcp-security",
        mode: "sync",
        action: "block",
        threshold: 0.8,
        enabled: true,
        config: {
          provider: "mcp_security",
          validate_inputs: "true",
          max_calls_per_request: 10,
          blocked_tools: "lookup",
        },
      },
    });
  });
});

// Of two stored entries with one name the push sends the later one, and the
// grid, the drawer and update-guardrail on a checks list read that one too.
// The drawer's save is built from that same entry.
describe("a drawer save where a name is stored twice", () => {
  const FIRST = {
    name: "mcp-security",
    enabled: false,
    threshold: 0.31,
    config: { blocked_tools: "first" },
  };
  const LAST = {
    name: "mcp-security",
    enabled: true,
    threshold: 1,
    config: { blocked_tools: "last", validate_inputs: "true" },
  };
  const editValidation = (v) => ({
    ...v,
    fields: { ...v.fields, validate_inputs: "false" },
  });

  it("edits the later rule of a rules list", () => {
    const body = drawerSave(
      { rules: clone([FIRST, LAST]) },
      "mcp-security",
      editValidation,
    );
    expect(body).toEqual({
      name: "mcp-security",
      config: { ...LAST, config: { ...LAST.config, validate_inputs: "false" } },
    });
  });

  it("edits the later entry of a checks list", () => {
    const body = drawerSave(
      { checks: clone([FIRST, LAST]) },
      "mcp-security",
      editValidation,
    );
    expect(body).toEqual({
      name: "mcp-security",
      config: { config: { ...LAST.config, validate_inputs: "false" } },
    });
  });

  it("edits the later entry under its own name when the names are aliases", () => {
    const saved = {
      checks: [
        { name: "pii-detector", enabled: true, threshold: 0.31 },
        { name: "pii-detection", enabled: true, threshold: 0.7 },
      ],
    };
    expect(openRow(saved, "pii-detection").values.threshold).toBe(0.7);
    expect(
      drawerSave(saved, "pii-detection", (v) => ({ ...v, threshold: 0.5 })),
    ).toEqual({ name: "pii-detection", config: { threshold: 0.5 } });
  });
});

// An Org config edit keeps each section's stored shape. Every other entry
// keeps its exact stored threshold (including null and unused spellings),
// not just an equivalent effective number.
describe("an Org config edit of another check", () => {
  const KEYWORDS = {
    name: "keyword-blocklist",
    enabled: true,
    config: { words: ["zeus"] },
  };

  it.each([
    { threshold: null },
    { confidence_threshold: 0.35, threshold: 0.9 },
    { confidence_threshold: 0.35 },
  ])("preserves a checks-list entry stored with %j", (fields) => {
    const saved = {
      checks: clone([
        { name: "pii-detector", enabled: true, ...fields },
        KEYWORDS,
      ]),
    };
    const change = renderOrgConfig(saved);
    fireEvent.click(within(cardFor("Keyword Blocklist")).getByRole("checkbox"));
    const patch = savedPatch(saved, change.mock.calls[0][0]);
    expect(patch.guardrails).toEqual({
      checks: [saved.checks[0], { ...KEYWORDS, enabled: false }],
    });
  });

  it("keeps a rule stored with a null threshold exactly as stored", () => {
    const pii = { name: "pii-detector", enabled: true, threshold: null };
    const saved = { rules: clone([pii, KEYWORDS]) };
    const change = renderOrgConfig(saved);
    fireEvent.click(within(cardFor("Keyword Blocklist")).getByRole("checkbox"));
    expect(savedPatch(saved, change.mock.calls[0][0])).toEqual({
      guardrails: { rules: [pii, { ...KEYWORDS, enabled: false }] },
    });
  });
});
