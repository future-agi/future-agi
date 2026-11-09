import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";

import GuardrailCheckDialog from "./GuardrailCheckDialog";
import { NOT_OFFERED_CHECKS } from "../guardrails/catalog";
import GuardrailConfigTab, {
  GUARDRAIL_CHECKS,
  MODEL_GUARDRAIL_CHECKS,
} from "./GuardrailConfigTab";
import { buildConfigPatch, guardrailsForSave } from "../hooks/useConfigPatch";

vi.mock("src/utils/logger", () => ({
  logger: { warn: vi.fn() },
}));

// These edit/save cases exercise an authorized actor; Member controls have their own gate tests.
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true, disabledReason: null }),
}));

// The Future AGI Eval dialog loads its Protect templates on open.
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn().mockResolvedValue({
      data: { result: [{ id: "1", name: "Toxicity" }] },
    }),
  },
  endpoints: { gateway: { protectTemplates: "/protect-templates/" } },
}));

const noop = () => {};

// These checks are not offered for editing. Exercise the serialization
// dialog directly so its preservation assertions survive, without turning a
// stored-only row into an editor. The tab's read-only gate is tested below.
const NOT_OFFERED = new Set([
  "Tool Permissions",
  "MCP Security",
  "DynamoAI Guard",
]);
const metaOf = (label) =>
  [...GUARDRAIL_CHECKS, ...MODEL_GUARDRAIL_CHECKS].find(
    (check) => check.label === label,
  );
function renderStoredDialog(label, name, stored, onChange) {
  render(
    <GuardrailCheckDialog
      open
      onClose={noop}
      checkName={name}
      initialData={stored}
      providerMeta={metaOf(label)}
      absentAction="log"
      absentThreshold={0}
      onSave={(checkName, data) => onChange({ checks: { [checkName]: data } })}
    />,
  );
}

const META = Object.fromEntries(
  [...GUARDRAIL_CHECKS, ...MODEL_GUARDRAIL_CHECKS].map((c) => [c.name, c]),
);

// A check as an org saves it with the credentials its form requires (a
// stored secret reads back masked): the Rules tab offers a partner check
// only then (partnerCheckConfigured).
const savedWithCredential = (name, check = { enabled: false }) => {
  const config = { ...(check.config || {}) };
  (META[name].fields || [])
    .filter((f) => f.type === "password" && f.required)
    .forEach((f) => {
      if (!(f.key in config) && !(f.key in check)) {
        config[f.key] = "__encrypted__0a1b2c3d";
      }
    });
  return { ...check, config };
};

// The checks the console keeps only as stored (constants/storedOnlyChecks.js),
// listed here so this file runs against a tree without that module too.
const STORED_ONLY_CHECKS = new Set([
  "llama-guard",
  "azure-content-safety",
  "presidio-pii",
  "bedrock-guardrails",
  "hiddenlayer-guard",
  "aporia-guard",
  "pangea-guard",
  "dynamoai-guard",
  "enkrypt-guard",
  "ibm-ai-detector",
  "grayswan-guard",
  "lasso-guard",
  "crowdstrike-aidr",
  "zscaler-guard",
  "tool-permissions",
]);

// Opens a stored check's dialog the way the console does: from its card on
// the Rules tab, or, for a check the tab does not offer (NOT_OFFERED_CHECKS)
// or lists read-only (STORED_ONLY_CHECKS), both kept as stored, the dialog
// itself with its catalog entry. Each save comes back through onChange as the
// tab gives it.
async function openStored(label, name, stored) {
  const onChange = vi.fn();
  if (NOT_OFFERED_CHECKS.has(name) || STORED_ONLY_CHECKS.has(name)) {
    render(
      <GuardrailCheckDialog
        open
        onClose={noop}
        onSave={(n, data) => onChange({ checks: { [n]: data } })}
        checkName={name}
        initialData={stored}
        providerMeta={META[name]}
        absentAction="log"
        absentThreshold={0}
      />,
    );
  } else {
    render(
      <GuardrailConfigTab
        guardrails={{ checks: { [name]: stored } }}
        onChange={onChange}
      />,
    );
    const card = screen.getByText(label).closest(".MuiCard-root");
    fireEvent.click(
      within(/** @type {HTMLElement} */ (card)).getAllByRole("button")[0],
    );
  }
  await screen.findByText(`Configure: ${label}`);
  return onChange;
}

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

// The Presidio card this repro opened is read-only now (STORED_ONLY_CHECKS);
// PII Detection's slug title-cases wrong the same way ("Pii Detection").
describe("GuardrailConfigTab -> dialog (TH-3989 repro)", () => {
  it("opens the PII Detection card with a correctly cased modal title", async () => {
    render(
      <GuardrailConfigTab
        guardrails={{ checks: { "pii-detection": { enabled: false } } }}
        onChange={noop}
      />,
    );

    const card = screen.getByText("PII Detection").closest(".MuiCard-root");
    expect(card).not.toBeNull();

    fireEvent.click(
      within(/** @type {HTMLElement} */ (card)).getAllByRole("button")[0],
    );

    expect(
      await screen.findByText("Configure: PII Detection"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Configure: Pii Detection")).toBeNull();
  });
});

// An org's checks run on the request only (the gateway runs every org check
// at its type's stage, and none of these three reads the answer), so no
// option may offer to check the model's response.
describe("GuardrailConfigTab -> dialog: no option checks the answer", () => {
  // A partner check's card is listed only once its account is stored, so a
  // new one's dialog is opened directly with its meta.
  const openCheck = async (label) => {
    if (NOT_OFFERED.has(label) || metaOf(label)?.provider) {
      renderStoredDialog(label, metaOf(label).name, null, noop);
      return screen.findByText(`Configure: ${label}`);
    }
    render(<GuardrailConfigTab guardrails={{ checks: {} }} onChange={noop} />);
    const card = screen.getByText(label).closest(".MuiCard-root");
    fireEvent.click(
      within(/** @type {HTMLElement} */ (card)).getByRole("button"),
    );
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
  // and never sent the Policies selection, so hallucination and RAG
  // validation, which judge an answer, could not apply.
  it("offers Aporia no hallucination or RAG policy, and does not claim them", async () => {
    await openCheck("Aporia Guardrails");
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByText(/hallucination/i)).toBeNull();
    expect(within(dialog).queryByText(/rag/i)).toBeNull();
    expect(within(dialog).queryByLabelText(/Policies/)).toBeNull();
    // The card (listed once Aporia's account is stored) shows the same
    // description as the dialog.
    expect(metaOf("Aporia Guardrails").description).not.toMatch(
      /hallucination|rag/i,
    );
    // The Rules tab lists a stored Aporia check read-only.
    render(
      <GuardrailConfigTab
        guardrails={{
          checks: { "aporia-guard": savedWithCredential("aporia-guard") },
        }}
        onChange={noop}
      />,
    );
    const card = screen
      .getByText("Aporia Guardrails", {
        selector: "h6, .MuiTypography-subtitle2",
      })
      .closest(".MuiCard-root");
    expect(
      within(/** @type {HTMLElement} */ (card)).queryByText(/hallucination/i),
    ).toBeNull();
  });

  // Groundedness judges whether a response is supported by its context; an
  // org's check would put the prompt to it.
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

// A check saved while the form still offered Text Type, Apply To or Validate
// Outputs keeps that value when it is re-saved, say to change its
// threshold: the form no longer shows those fields, so dropping their values
// would change what the check does without the admin seeing why.
describe("GuardrailConfigTab -> dialog: a re-save keeps settings the form no longer shows", () => {
  const resave = async (label, name, check) => {
    const onChange = vi.fn();
    if (NOT_OFFERED.has(label)) {
      renderStoredDialog(label, name, check, onChange);
      await screen.findByText(`Configure: ${label}`);
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      expect(onChange).toHaveBeenCalledTimes(1);
      return onChange.mock.calls[0][0].checks[name].config;
    }
    render(
      <GuardrailConfigTab
        guardrails={{ checks: { [name]: check } }}
        onChange={onChange}
      />,
    );
    const card = screen.getByText(label).closest(".MuiCard-root");
    fireEvent.click(
      within(/** @type {HTMLElement} */ (card)).getAllByRole("button")[0],
    );
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
    renderStoredDialog(
      "Tool Permissions",
      "tool-permissions",
      {
        enabled: true,
        action: "block",
        confidence_threshold: 0.8,
        provider: "tool_permission",
        config: { mode: "denylist", tools: "db_write", apply_to: "both" },
      },
      onChange,
    );
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
// the settings the form no longer shows (DynamoAI's text_type, Tool
// Permissions' apply_to, MCP Security's validate_outputs), the ones it shows
// with a default when they are absent (a threshold, an action,
// max_calls_per_request), values it would normalize (untrimmed text), keys it
// knows nothing of, where each key is kept, and the keys' order.
describe("GuardrailConfigTab -> dialog: a re-save changes only the fields edited", () => {
  const open = async (label, name, stored) => {
    const onChange = vi.fn();
    if (NOT_OFFERED.has(label)) {
      renderStoredDialog(label, name, stored, onChange);
      await screen.findByText(`Configure: ${label}`);
      return onChange;
    }
    render(
      <GuardrailConfigTab
        guardrails={{ checks: { [name]: stored } }}
        onChange={onChange}
      />,
    );
    const card = screen.getByText(label).closest(".MuiCard-root");
    fireEvent.click(
      within(/** @type {HTMLElement} */ (card)).getAllByRole("button")[0],
    );
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
    fireEvent.change(screen.getByLabelText(/Max Calls Per Request/), {
      target: { value: "3" },
    });
    expect(save(onChange, name)).toBe(
      JSON.stringify({
        ...stored,
        config: { validate_outputs: "true", max_calls_per_request: 3 },
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
    const card = screen.getByText("Keyword Blocklist").closest(".MuiCard-root");
    fireEvent.click(
      within(/** @type {HTMLElement} */ (card)).getAllByRole("button")[0],
    );
    await screen.findByText("Configure: Keyword Blocklist");
    fireEvent.change(screen.getByLabelText(/Blocked Keywords/), {
      target: { value: "db_write" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onChange.mock.calls[0][0].checks["keyword-blocklist"]).toEqual({
      enabled: true,
      action: "block",
      confidence_threshold: 0.8,
      config: { words: ["db_write"] },
    });
  });
});

// The whole save path, as GuardrailManagementSection and OrgConfigEditor run
// it: the rules editor's draft goes through guardrailsForSave, and the patch
// against the saved section is what update-config gets.
describe("GuardrailConfigTab -> dialog -> save: a re-save keeps what every check does", () => {
  // A section an older console stored as a checks map, in the shape the
  // gateway contract takes (a check holds enabled, action,
  // confidence_threshold and config; its provider is in its config). The
  // config push forwards it to the gateway as stored, so Lakera (no action,
  // no threshold) runs as log at threshold 0, and data leakage (block, no
  // threshold) blocks any score above 0. Lakera is a partner check the tab
  // still offers; the read-only partner checks (STORED_ONLY_CHECKS) have no
  // Configure or switch, below.
  const saved = {
    checks: {
      "lakera-guard": {
        enabled: true,
        config: {
          provider: "lakera",
          endpoint: "https://guard.example.test/v2/guard",
          api_key: "__encrypted__",
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
  const card = (label) =>
    /** @type {HTMLElement} */ (
      screen.getByText(label).closest(".MuiCard-root")
    );
  const editLakera = async () => {
    const onChange = renderTab(saved);
    fireEvent.click(
      within(card("Lakera Guard")).getByRole("button", { name: /Configure/ }),
    );
    await screen.findByText("Configure: Lakera Guard");
    return onChange;
  };
  const patchOf = (before, toSave) =>
    buildConfigPatch({ guardrails: before }, { guardrails: toSave }, [
      "guardrails",
    ]);

  it("saves nothing when nothing was edited", async () => {
    const onChange = await editLakera();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(guardrailsForSave(onChange.mock.calls[0][0], saved)).toBe(saved);
  });

  it("saves an edit of one field as that field alone, adding no action or threshold", async () => {
    const onChange = await editLakera();
    fireEvent.change(screen.getByLabelText(/Endpoint URL/), {
      target: { value: "https://guard-2.example.test/v2/guard" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const toSave = guardrailsForSave(onChange.mock.calls[0][0], saved);
    expect(toSave).toEqual({
      checks: {
        "lakera-guard": {
          ...saved.checks["lakera-guard"],
          config: {
            ...saved.checks["lakera-guard"].config,
            endpoint: "https://guard-2.example.test/v2/guard",
          },
        },
        "data-leakage-prevention": { enabled: true, action: "block" },
      },
    });
    expect(patchOf(saved, toSave)).toEqual({
      guardrails: {
        checks: {
          "lakera-guard": {
            config: { endpoint: "https://guard-2.example.test/v2/guard" },
          },
        },
      },
    });
  });

  it("saves a check switched on with its provider in its config, as the gateway contract takes it", () => {
    const stored = {
      checks: {
        ...saved.checks,
        "lakera-guard": { ...saved.checks["lakera-guard"], enabled: false },
      },
    };
    const onChange = renderTab(stored);
    // The card's switch gives the check a provider of its own.
    fireEvent.click(within(card("Lakera Guard")).getByRole("checkbox"));
    const draft = onChange.mock.calls[0][0];
    expect(draft.checks["lakera-guard"].provider).toBe("lakera");

    const toSave = guardrailsForSave(draft, stored);
    expect(toSave.checks["lakera-guard"]).toEqual(saved.checks["lakera-guard"]);
    expect(patchOf(stored, toSave)).toEqual({
      guardrails: { checks: { "lakera-guard": { enabled: true } } },
    });
  });

  it("gives a stored DynamoAI check, which the tab lists read-only, no switch and no Configure", () => {
    const stored = {
      checks: {
        "dynamoai-guard": {
          enabled: false,
          config: {
            provider: "dynamoai",
            api_key: "__encrypted__",
            policy_ids: "p1",
          },
        },
      },
    };
    renderTab(stored);
    const dynamo = card("DynamoAI Guard");
    expect(within(dynamo).queryByRole("checkbox")).toBeNull();
    expect(
      within(dynamo).queryByRole("button", { name: /Configure/ }),
    ).toBeNull();
    expect(within(dynamo).getByText("Off")).toBeInTheDocument();
  });
});

describe("GuardrailConfigTab -> dialog Base URL", () => {
  // The tab lists a partner check's card only once its account is stored
  // (partnerConfigured), and CrowdStrike AIDR only read-only, so the dialog
  // is opened directly with its catalog entry, as the card opens it; a save
  // comes back through onChange as the tab gives it.
  const openCard = async (label, guardrails) => {
    const onChange = vi.fn();
    const { name } = metaOf(label);
    renderStoredDialog(label, name, guardrails?.checks?.[name], onChange);
    await screen.findByText(`Configure: ${label}`);
    return onChange;
  };

  // An empty Base URL means "this platform": the backend fills its own
  // BASE_URL and injects the org's keys. A stored api.futureagi.com on a
  // self-hosted install is cross-environment, so no keys are injected and
  // the check fails on every request.
  it.each([
    ["a saved check", { enabled: true, config: { eval_ids: ["1"] } }],
    [
      "a saved check with an empty URL",
      { enabled: true, config: { eval_ids: ["1"], base_url: "" } },
    ],
    ["a new check", undefined],
  ])("leaves Future AGI Eval Base URL empty for %s", async (_, existing) => {
    const onChange = await openCard("Future AGI Eval", {
      checks: existing ? { "futureagi-eval": existing } : {},
    });

    expect(screen.getByRole("textbox", { name: "Base URL" })).toHaveValue("");
    expect(
      screen.getByPlaceholderText("https://api.futureagi.com"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onChange).toHaveBeenCalledTimes(1);
    const saved = onChange.mock.calls[0][0].checks["futureagi-eval"];
    // No URL is saved: absent and empty both mean this platform
    // (config_push._configure_fi_auth), and a re-save keeps a stored empty
    // one as it is stored, as it keeps every field the user did not edit.
    expect(saved.config?.base_url ?? "").toBe("");
    if (existing) expect(saved.config.eval_ids).toEqual(["1"]);
  });

  it("keeps a Base URL the user typed for Future AGI Eval", async () => {
    const onChange = await openCard("Future AGI Eval", { checks: {} });

    fireEvent.change(screen.getByRole("textbox", { name: "Base URL" }), {
      target: { value: "https://dev.api.futureagi.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const saved = onChange.mock.calls[0][0].checks["futureagi-eval"];
    expect(saved.config.base_url).toBe("https://dev.api.futureagi.com");
  });

  it.each(["https://api.futureagi.com", "https://dev.api.futureagi.com"])(
    "preserves an explicitly saved Future AGI Eval URL: %s",
    async (baseUrl) => {
      const onChange = await openCard("Future AGI Eval", {
        checks: {
          "futureagi-eval": {
            enabled: true,
            config: { eval_ids: ["1"], base_url: baseUrl },
          },
        },
      });

      expect(screen.getByRole("textbox", { name: "Base URL" })).toHaveValue(
        baseUrl,
      );
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      const saved = onChange.mock.calls[0][0].checks["futureagi-eval"];
      expect(saved.config).toEqual({ eval_ids: ["1"], base_url: baseUrl });
    },
  );

  it("lets a user clear a saved URL to use this platform", async () => {
    const onChange = await openCard("Future AGI Eval", {
      checks: {
        "futureagi-eval": {
          enabled: true,
          config: { eval_ids: ["1"], base_url: "https://api.futureagi.com" },
        },
      },
    });

    fireEvent.change(screen.getByRole("textbox", { name: "Base URL" }), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const saved = onChange.mock.calls[0][0].checks["futureagi-eval"];
    expect(saved.config).toEqual({ eval_ids: ["1"] });
  });

  it("still fills and saves other providers' default URL for a new check", async () => {
    const onChange = await openCard("CrowdStrike AIDR", { checks: {} });

    expect(screen.getByRole("textbox", { name: "Base URL" })).toHaveValue(
      "https://api.crowdstrike.com",
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const saved = onChange.mock.calls[0][0].checks["crowdstrike-aidr"];
    expect(saved.config.base_url).toBe("https://api.crowdstrike.com");
  });

  it("still shows other providers' default URL for a saved check, and saves only what was edited", async () => {
    const existing = { enabled: true, config: { base_url: "" } };
    const onChange = await openCard("CrowdStrike AIDR", {
      checks: { "crowdstrike-aidr": existing },
    });

    expect(screen.getByRole("textbox", { name: "Base URL" })).toHaveValue(
      "https://api.crowdstrike.com",
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // A re-save changes only the fields edited (see "a re-save changes only
    // the fields edited" above), so the untouched stored URL stays as stored.
    const saved = onChange.mock.calls[0][0].checks["crowdstrike-aidr"];
    expect(saved.config.base_url).toBe("");
  });
});
