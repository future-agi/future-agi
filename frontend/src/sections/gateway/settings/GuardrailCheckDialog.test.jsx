import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";

import GuardrailCheckDialog from "./GuardrailCheckDialog";
import GuardrailConfigTab from "./GuardrailConfigTab";
import { buildConfigPatch, guardrailsForSave } from "../hooks/useConfigPatch";

vi.mock("src/utils/logger", () => ({
  logger: { warn: vi.fn() },
}));

const noop = () => {};

const renderDialog = (props = {}) =>
  render(
    <GuardrailCheckDialog
      open
      onClose={noop}
      onSave={noop}
      checkName="presidio-pii"
      initialData={null}
      providerMeta={null}
      {...props}
    />,
  );

describe("GuardrailCheckDialog title", () => {
  // The slug cannot be title-cased into the right name. Two ways it breaks:
  // acronyms ("presidio-pii" -> "Presidio Pii") and vendor names that simply
  // differ from the slug ("bedrock-guardrails" is "AWS Bedrock Guardrails").
  // 15 of the 28 checks were affected, so the title must come from the label.
  it.each([
    ["presidio-pii", "Presidio PII"],
    ["pii-detection", "PII Detection"],
    ["mcp-security", "MCP Security"],
    ["crowdstrike-aidr", "CrowdStrike AIDR"],
    ["bedrock-guardrails", "AWS Bedrock Guardrails"],
    ["enkrypt-guard", "Enkrypt AI"],
  ])("uses the provider label for %s", (checkName, label) => {
    renderDialog({ checkName, providerMeta: { label } });

    expect(screen.getByText(`Configure: ${label}`)).toBeInTheDocument();
  });

  it("does not title-case the slug when a label is available", () => {
    renderDialog({
      checkName: "presidio-pii",
      providerMeta: { label: "Presidio PII" },
    });

    expect(screen.queryByText("Configure: Presidio Pii")).toBeNull();
  });

  it("falls back to the slug when the dialog opens without provider meta", () => {
    renderDialog({ checkName: "keyword-blocklist", providerMeta: null });

    expect(
      screen.getByText("Configure: Keyword Blocklist"),
    ).toBeInTheDocument();
  });
});

describe("GuardrailConfigTab -> dialog (TH-3989 repro)", () => {
  it("opens the Presidio card with a correctly cased modal title", async () => {
    render(<GuardrailConfigTab guardrails={{ checks: {} }} onChange={noop} />);

    const card = screen.getByText("Presidio PII").closest(".MuiCard-root");
    expect(card).not.toBeNull();

    fireEvent.click(within(card).getByRole("button"));

    expect(
      await screen.findByText("Configure: Presidio PII"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Configure: Presidio Pii")).toBeNull();
  });
});

// An org's checks run on the request only (the gateway runs every org check
// at its type's stage, and none of these three reads the answer), so no
// option may offer to check the model's response.
describe("GuardrailConfigTab -> dialog: no option checks the answer", () => {
  const openCheck = async (label) => {
    render(<GuardrailConfigTab guardrails={{ checks: {} }} onChange={noop} />);
    const card = screen.getByText(label).closest(".MuiCard-root");
    fireEvent.click(within(card).getByRole("button"));
    return screen.findByText(`Configure: ${label}`);
  };

  it("offers DynamoAI no MODEL_RESPONSE text type", async () => {
    await openCheck("DynamoAI Guard");
    expect(screen.queryByLabelText(/Text Type/)).toBeNull();
    expect(screen.queryByText("MODEL_RESPONSE")).toBeNull();
  });

  it("offers Tool Permissions no response or both target", async () => {
    await openCheck("Tool Permissions");
    expect(screen.queryByLabelText(/Apply To/)).toBeNull();
    expect(screen.getByLabelText(/Tool Patterns/)).toBeInTheDocument();
  });

  it("offers MCP Security no output validation, and does not claim it", async () => {
    await openCheck("MCP Security");
    expect(screen.queryByLabelText(/Validate Outputs/)).toBeNull();
    expect(screen.getByLabelText(/Validate Inputs/)).toBeInTheDocument();
    expect(screen.queryByText(/outputs/i)).toBeNull();
  });

  // The gateway sends Aporia the request text (validation_target "prompt")
  // and no Policies selection, and hallucination and RAG validation judge an
  // answer, so the dialog offers neither.
  it("offers Aporia no hallucination or RAG policy, and does not claim them", async () => {
    await openCheck("Aporia Guardrails");
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByText(/hallucination/i)).toBeNull();
    expect(within(dialog).queryByText(/rag/i)).toBeNull();
    expect(within(dialog).queryByLabelText(/Policies/)).toBeNull();
    const card = screen
      .getByText("Aporia Guardrails", {
        selector: "h6, .MuiTypography-subtitle2",
      })
      .closest(".MuiCard-root");
    expect(within(card).queryByText(/hallucination/i)).toBeNull();
  });

  // Groundedness judges whether a response is supported by its context, and
  // an org's check reads the request only, so it is not offered.
  it("offers IBM Granite Guardian no groundedness criterion", async () => {
    await openCheck("IBM Granite Guardian");
    fireEvent.mouseDown(
      within(screen.getByRole("dialog")).getByRole("combobox", {
        name: /Risk Criteria/,
      }),
    );
    expect(
      await screen.findByRole("option", { name: "harm" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "groundedness" })).toBeNull();
  });
});

// A check stored with Text Type, Apply To or Validate Outputs keeps that
// value when it is re-saved, say to change its threshold: the form does not
// show those fields, and a re-save keeps every value the admin did not see.
describe("GuardrailConfigTab -> dialog: a re-save keeps settings the form does not show", () => {
  const resave = async (label, name, check) => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={{ checks: { [name]: check } }}
        onChange={onChange}
      />,
    );
    const card = screen.getByText(label).closest(".MuiCard-root");
    fireEvent.click(within(card).getAllByRole("button")[0]);
    await screen.findByText(`Configure: ${label}`);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onChange).toHaveBeenCalledTimes(1);
    return onChange.mock.calls[0][0].checks[name].config;
  };

  it("keeps Tool Permissions' apply_to", async () => {
    const config = await resave("Tool Permissions", "tool-permissions", {
      enabled: true,
      action: "block",
      confidence_threshold: 0.8,
      provider: "tool_permission",
      config: { mode: "denylist", tools: "db_write", apply_to: "response" },
    });
    expect(config).toEqual({
      mode: "denylist",
      tools: "db_write",
      apply_to: "response",
    });
  });

  it("keeps DynamoAI's text_type and its stored key", async () => {
    const config = await resave("DynamoAI Guard", "dynamoai-guard", {
      enabled: true,
      action: "block",
      confidence_threshold: 0.8,
      provider: "dynamoai",
      config: {
        api_key: "dk-stored",
        endpoint: "https://api.dynamofl.com/moderation/analyze",
        policy_ids: "p1",
        text_type: "MODEL_RESPONSE",
      },
    });
    expect(config).toEqual({
      api_key: "dk-stored",
      endpoint: "https://api.dynamofl.com/moderation/analyze",
      policy_ids: "p1",
      text_type: "MODEL_RESPONSE",
    });
  });

  it("keeps MCP Security's validate_outputs", async () => {
    const config = await resave("MCP Security", "mcp-security", {
      enabled: true,
      action: "block",
      confidence_threshold: 0.8,
      provider: "mcp_security",
      config: {
        validate_inputs: "true",
        validate_outputs: "false",
        max_calls_per_request: 5,
      },
    });
    expect(config).toEqual({
      validate_inputs: "true",
      validate_outputs: "false",
      max_calls_per_request: 5,
    });
  });

  it("still drops a field the admin cleared", async () => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={{
          checks: {
            "tool-permissions": {
              enabled: true,
              action: "block",
              confidence_threshold: 0.8,
              provider: "tool_permission",
              config: { mode: "denylist", tools: "db_write", apply_to: "both" },
            },
          },
        }}
        onChange={onChange}
      />,
    );
    const card = screen.getByText("Tool Permissions").closest(".MuiCard-root");
    fireEvent.click(within(card).getAllByRole("button")[0]);
    await screen.findByText("Configure: Tool Permissions");
    fireEvent.change(screen.getByLabelText(/Tool Patterns/), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onChange.mock.calls[0][0].checks["tool-permissions"].config).toEqual(
      { mode: "denylist", apply_to: "both" },
    );
  });
});

// A re-save must never change what a check does. The saved check comes back
// byte for byte as it was stored, apart from the fields the admin edited:
// the settings the form does not show (DynamoAI's text_type, Tool
// Permissions' apply_to, MCP Security's validate_outputs), the ones it shows
// with a default when they are absent (a threshold, an action,
// max_calls_per_request), values it would normalize (untrimmed text), keys it
// knows nothing of, where each key is kept, and the keys' order.
describe("GuardrailConfigTab -> dialog: a re-save changes only the fields edited", () => {
  const open = async (label, name, stored) => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={{ checks: { [name]: stored } }}
        onChange={onChange}
      />,
    );
    const card = screen.getByText(label).closest(".MuiCard-root");
    fireEvent.click(within(card).getAllByRole("button")[0]);
    await screen.findByText(`Configure: ${label}`);
    return onChange;
  };
  const save = (onChange, name) => {
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onChange).toHaveBeenCalledTimes(1);
    return JSON.stringify(onChange.mock.calls[0][0].checks[name]);
  };

  const storedChecks = [
    [
      "Tool Permissions",
      "tool-permissions",
      {
        provider: "tool_permission",
        config: { tools: "db_write", apply_to: "response", mode: "denylist" },
        enabled: true,
        action: "block",
        confidence_threshold: 0.8,
      },
    ],
    [
      "DynamoAI Guard",
      "dynamoai-guard",
      {
        enabled: true,
        provider: "dynamoai",
        config: {
          text_type: "MODEL_RESPONSE",
          api_key: "dk-stored",
          policy_ids: " p1, p2 ",
        },
      },
    ],
    [
      "MCP Security",
      "mcp-security",
      {
        enabled: true,
        action: "warn",
        provider: "mcp_security",
        stage: "pre",
        _note: "kept by the platform",
        config: { validate_outputs: "true", blocked_tools: "shell" },
      },
    ],
  ];

  it.each(storedChecks)(
    "saves %s back byte for byte when nothing was edited",
    async (label, name, stored) => {
      const onChange = await open(label, name, stored);
      expect(save(onChange, name)).toBe(JSON.stringify(stored));
    },
  );

  it("changes only the action when the action is edited", async () => {
    const [label, name, stored] = storedChecks[2];
    const onChange = await open(label, name, stored);
    fireEvent.mouseDown(
      within(screen.getByRole("dialog")).getByRole("combobox", {
        name: /Action/,
      }),
    );
    fireEvent.click(await screen.findByRole("option", { name: "Block" }));
    expect(save(onChange, name)).toBe(
      JSON.stringify({ ...stored, action: "block" }),
    );
  });

  it("changes only the field edited, in its place", async () => {
    const [label, name, stored] = storedChecks[0];
    const onChange = await open(label, name, stored);
    fireEvent.change(screen.getByLabelText(/Tool Patterns/), {
      target: { value: "db_write, file_*" },
    });
    expect(save(onChange, name)).toBe(
      JSON.stringify({
        ...stored,
        config: { ...stored.config, tools: "db_write, file_*" },
      }),
    );
  });

  it("adds a field the admin filled in, and removes one cleared", async () => {
    const [label, name, stored] = storedChecks[2];
    const onChange = await open(label, name, stored);
    fireEvent.change(screen.getByLabelText(/Blocked Tools/), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText(/Allowed Servers/), {
      target: { value: "github" },
    });
    expect(save(onChange, name)).toBe(
      JSON.stringify({
        ...stored,
        config: { validate_outputs: "true", allowed_servers: "github" },
      }),
    );
  });

  it("writes an edited field stored at the top level where it is", async () => {
    const stored = {
      enabled: true,
      tools: "db_write",
      mode: "allowlist",
      apply_to: "both",
    };
    const onChange = await open("Tool Permissions", "tool-permissions", stored);
    fireEvent.change(screen.getByLabelText(/Tool Patterns/), {
      target: { value: "db_read" },
    });
    expect(save(onChange, "tool-permissions")).toBe(
      JSON.stringify({ ...stored, tools: "db_read" }),
    );
  });

  it("keeps a stored credential and leaves it out of nothing else", async () => {
    const [label, name, stored] = storedChecks[1];
    const onChange = await open(label, name, stored);
    // The credential shows masked; the request never carries the mask.
    expect(screen.getByLabelText(/API Key/)).toHaveValue("••••••••");
    expect(save(onChange, name)).toBe(JSON.stringify(stored));
  });

  it("still builds a new check from the form, defaults included", async () => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab guardrails={{ checks: {} }} onChange={onChange} />,
    );
    const card = screen.getByText("Tool Permissions").closest(".MuiCard-root");
    fireEvent.click(within(card).getAllByRole("button")[0]);
    await screen.findByText("Configure: Tool Permissions");
    fireEvent.change(screen.getByLabelText(/Tool Patterns/), {
      target: { value: "db_write" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onChange.mock.calls[0][0].checks["tool-permissions"]).toEqual({
      enabled: true,
      action: "block",
      confidence_threshold: 0.8,
      provider: "tool_permission",
      config: { mode: "denylist", tools: "db_write" },
    });
  });
});

// The whole save path, as GuardrailManagementSection and OrgConfigEditor run
// it: the rules editor's draft goes through guardrailsForSave, and the patch
// against the saved section is what update-config gets.
describe("GuardrailConfigTab -> dialog -> save: a re-save keeps what every check does", () => {
  // A section stored as a checks map, in the shape the
  // gateway contract takes (a check holds enabled, action,
  // confidence_threshold and config; its provider is in its config). The
  // config push forwards it to the gateway as stored, so DynamoAI (no
  // action, no threshold) runs as log at threshold 0, and data leakage
  // (block, no threshold) blocks any score above 0.
  const saved = {
    checks: {
      "dynamoai-guard": {
        enabled: true,
        config: {
          provider: "dynamoai",
          text_type: "MODEL_RESPONSE",
          api_key: "__encrypted__",
          policy_ids: "p1",
        },
        _policy: "policy-1",
      },
      "data-leakage-prevention": { enabled: true, action: "block" },
    },
  };
  const renderTab = (guardrails) => {
    const onChange = vi.fn();
    render(<GuardrailConfigTab guardrails={guardrails} onChange={onChange} />);
    return onChange;
  };
  const card = (label) => screen.getByText(label).closest(".MuiCard-root");
  const editDynamo = async () => {
    const onChange = renderTab(saved);
    fireEvent.click(within(card("DynamoAI Guard")).getAllByRole("button")[0]);
    await screen.findByText("Configure: DynamoAI Guard");
    return onChange;
  };
  const patchOf = (before, toSave) =>
    buildConfigPatch({ guardrails: before }, { guardrails: toSave }, [
      "guardrails",
    ]);

  it("saves nothing when nothing was edited", async () => {
    const onChange = await editDynamo();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(guardrailsForSave(onChange.mock.calls[0][0], saved)).toBe(saved);
  });

  it("saves an edit of one field as that field alone, adding no action or threshold", async () => {
    const onChange = await editDynamo();
    fireEvent.change(screen.getByLabelText(/Policy IDs/), {
      target: { value: "p1, p2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const toSave = guardrailsForSave(onChange.mock.calls[0][0], saved);
    expect(toSave).toEqual({
      checks: {
        "dynamoai-guard": {
          ...saved.checks["dynamoai-guard"],
          config: {
            ...saved.checks["dynamoai-guard"].config,
            policy_ids: "p1, p2",
          },
        },
        "data-leakage-prevention": { enabled: true, action: "block" },
      },
    });
    expect(patchOf(saved, toSave)).toEqual({
      guardrails: {
        checks: { "dynamoai-guard": { config: { policy_ids: "p1, p2" } } },
      },
    });
  });

  it("saves a check switched on with its provider in its config, as the gateway contract takes it", () => {
    const stored = {
      checks: {
        ...saved.checks,
        "dynamoai-guard": { ...saved.checks["dynamoai-guard"], enabled: false },
      },
    };
    const onChange = renderTab(stored);
    // The card's switch gives the check a provider of its own.
    fireEvent.click(within(card("DynamoAI Guard")).getByRole("checkbox"));
    const draft = onChange.mock.calls[0][0];
    expect(draft.checks["dynamoai-guard"].provider).toBe("dynamoai");

    const toSave = guardrailsForSave(draft, stored);
    expect(toSave.checks["dynamoai-guard"]).toEqual(
      saved.checks["dynamoai-guard"],
    );
    expect(patchOf(stored, toSave)).toEqual({
      guardrails: { checks: { "dynamoai-guard": { enabled: true } } },
    });
  });
});
