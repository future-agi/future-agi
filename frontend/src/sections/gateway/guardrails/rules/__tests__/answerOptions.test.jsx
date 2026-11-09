import React from "react";
import { describe, it, expect, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";

import { renderPage } from "../../__tests__/helpers";
import { catalogEntry } from "../../catalog";
import {
  formValuesFor,
  ruleBodyForSave,
  storedActionOf,
} from "../../catalog/rule-model";
import AddCheckDialog from "../AddCheckDialog";
import RuleDrawer from "../RuleDrawer";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// An organization's checks run on the request only: the gateway runs every
// org check at its type's stage, and none of these partner checks reads the
// model's answer. So neither Add check nor the rule drawer may offer a
// setting that checks the response, and a new check stores none.

const noop = () => {};

async function openNewCheck(name) {
  const meta = catalogEntry(name);
  renderPage(
    <RuleDrawer open meta={meta} row={null} onClose={noop} onSave={noop} />,
  );
  return screen.findByRole("dialog", { name: meta.label });
}

function addCheckPicker() {
  renderPage(
    <AddCheckDialog open onClose={noop} onPick={noop} configured={new Set()} />,
  );
  const picker = screen.getByRole("dialog", { name: "Add a check" });
  return within(picker);
}

describe("Add check and the rule drawer: no option checks the answer", () => {
  it("offers DynamoAI no MODEL_RESPONSE text type", async () => {
    const drawer = await openNewCheck("dynamoai-guard");
    expect(within(drawer).queryByLabelText(/Text Type/)).toBeNull();
    expect(within(drawer).queryByText("MODEL_RESPONSE")).toBeNull();
    expect(within(drawer).getByLabelText(/Policy IDs/)).toBeInTheDocument();
  });

  it("offers Tool Permissions no response or both target", async () => {
    const drawer = await openNewCheck("tool-permissions");
    expect(within(drawer).queryByLabelText(/Apply To/)).toBeNull();
    expect(within(drawer).getByLabelText(/Tool Patterns/)).toBeInTheDocument();
  });

  it("offers MCP Security no output validation, and does not claim it", async () => {
    const drawer = await openNewCheck("mcp-security");
    expect(within(drawer).queryByLabelText(/Validate Outputs/)).toBeNull();
    expect(
      within(drawer).getByLabelText(/Validate Inputs/),
    ).toBeInTheDocument();
    expect(within(drawer).queryByText(/outputs/i)).toBeNull();
  });

  it("lists neither MCP Security nor Tool Permissions in Add check", () => {
    renderPage(
      <AddCheckDialog
        open
        onClose={noop}
        onPick={noop}
        configured={new Set()}
      />,
    );
    const picker = screen.getByRole("dialog", { name: "Add a check" });
    expect(within(picker).queryByText("MCP Security")).toBeNull();
    expect(within(picker).queryByText("Tool Permissions")).toBeNull();
  });

  // The gateway sends Aporia the request text (validation_target "prompt")
  // and never sent a Policies selection, so hallucination and RAG
  // validation, which judge an answer, could not apply.
  it("offers Aporia no hallucination or RAG policy, and does not claim them", async () => {
    const drawer = await openNewCheck("aporia-guard");
    expect(within(drawer).queryByText(/hallucination/i)).toBeNull();
    expect(within(drawer).queryByText(/rag/i)).toBeNull();
    expect(within(drawer).queryByLabelText(/Policies/)).toBeNull();
    expect(within(drawer).getByLabelText(/Project ID/)).toBeInTheDocument();
  });

  it("lists no Aporia check in Add check without a configured account", () => {
    expect(addCheckPicker().queryByText("Aporia Guardrails")).toBeNull();
  });

  // Groundedness judges whether a response is supported by its context; an
  // org's check would put the prompt to it.
  it("offers IBM Granite Guardian no groundedness criterion", async () => {
    const drawer = await openNewCheck("ibm-ai-detector");
    fireEvent.mouseDown(
      within(drawer).getByRole("combobox", { name: /Risk Criteria/ }),
    );
    expect(
      await screen.findByRole("option", { name: "harm" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "groundedness" })).toBeNull();
  });
});

// A new check is saved from the form's starting values plus what the admin
// types. None of those values may carry a setting the gateway never reads.
describe("a new check stores no setting that checks the answer", () => {
  const newRuleConfig = (name, typed = {}) => {
    const meta = catalogEntry(name);
    const form = formValuesFor(meta, null, (check) => storedActionOf(check));
    return ruleBodyForSave({
      saved: { rules: [] },
      check: null,
      meta,
      catalogName: name,
      form: { ...form, fields: { ...form.fields, ...typed } },
    }).config.config;
  };

  it.each([
    ["tool-permissions", "apply_to", { tools: "db_write" }],
    ["mcp-security", "validate_outputs", {}],
    ["dynamoai-guard", "text_type", { api_key: "dk", policy_ids: "p1" }],
    ["aporia-guard", "policies", { api_key: "ak", project_id: "proj" }],
  ])("a new %s check is saved without %s", (name, key, typed) => {
    expect(newRuleConfig(name, typed)).not.toHaveProperty(key);
  });

  it("saves a new Tool Permissions check with only its mode and patterns", () => {
    expect(newRuleConfig("tool-permissions", { tools: "db_write" })).toEqual({
      mode: "denylist",
      tools: "db_write",
      provider: "tool_permission",
    });
  });
});

// A check saved while the form still offered one of these settings keeps the
// stored value when it is re-saved, say to change its threshold: the form no
// longer shows the field, so dropping its value would change what the check
// does without the admin seeing why. The gateway leaves it unused.
describe("a re-save keeps settings the form no longer shows", () => {
  const resave = async (name, storedConfig) => {
    const meta = catalogEntry(name);
    const check = {
      enabled: true,
      action: "block",
      threshold: 0.8,
      config: storedConfig,
    };
    const onSave = vi.fn(() => Promise.resolve());
    renderPage(
      <RuleDrawer
        open
        meta={meta}
        row={{
          id: name,
          ruleName: name,
          label: meta.label,
          kind: meta.kind,
          stage: meta.stage,
          check,
          triggers: 0,
          blocked: 0,
          accuracy: null,
        }}
        onClose={noop}
        onSave={onSave}
      />,
    );
    await screen.findByRole("dialog", { name: meta.label });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledTimes(1);
    const rule = { name, mode: "sync", ...check };
    return ruleBodyForSave({
      saved: { rules: [rule] },
      check: { ...check, name },
      meta,
      catalogName: name,
      form: onSave.mock.calls[0][0],
    }).config.config;
  };

  it("keeps Tool Permissions' apply_to", async () => {
    const stored = {
      provider: "tool_permission",
      mode: "denylist",
      tools: "db_write",
      apply_to: "response",
    };
    expect(await resave("tool-permissions", stored)).toEqual(stored);
  });

  it("keeps DynamoAI's text_type and its stored key", async () => {
    const stored = {
      provider: "dynamoai",
      api_key: "__encrypted__",
      endpoint: "https://api.dynamofl.com/moderation/analyze",
      policy_ids: "p1",
      text_type: "MODEL_RESPONSE",
    };
    expect(await resave("dynamoai-guard", stored)).toEqual(stored);
  });

  it("keeps MCP Security's validate_outputs", async () => {
    const stored = {
      provider: "mcp_security",
      validate_inputs: "true",
      validate_outputs: "false",
      max_calls_per_request: 5,
    };
    expect(await resave("mcp-security", stored)).toEqual(stored);
  });

  it("keeps Aporia's policies", async () => {
    const stored = {
      provider: "aporia",
      api_key: "__encrypted__",
      endpoint: "https://gr-prd.aporia.com",
      project_id: "proj",
      policies: ["hallucination", "injection", "pii"],
    };
    expect(await resave("aporia-guard", stored)).toEqual(stored);
  });
});
