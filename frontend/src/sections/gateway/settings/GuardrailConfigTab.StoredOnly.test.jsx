// The Rules tab and the Org config editor's Guardrails tab list a partner
// check this release does not offer (STORED_ONLY_CHECKS) only as the org
// saved it: read-only, with no switch to turn it on and no Configure, never
// as a way to add one. An Owner or Admin can turn a stored one off or remove
// it, each after a confirmation, and either changes only that check. Saving
// anything else sends every stored-only check back exactly as it is stored,
// in each shape a section is stored in.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "src/utils/test-utils";

import GuardrailConfigTab, {
  MODEL_GUARDRAIL_CHECKS,
} from "./GuardrailConfigTab";
import { buildConfigPatch, guardrailsForSave } from "../hooks/useConfigPatch";

vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));

let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

const META = Object.fromEntries(MODEL_GUARDRAIL_CHECKS.map((c) => [c.name, c]));
// The checks the console keeps only as stored (constants/storedOnlyChecks.js),
// listed here so this file also runs, and fails, against the tree before them.
const STORED_ONLY_CHECKS = [
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
];
// The fourteen partner checks. Tool Permissions' read-only summary and
// confirmed actions have their own coverage in GuardrailStoredSummary.test.jsx.
const PARTNERS = STORED_ONLY_CHECKS.filter((n) => n !== "tool-permissions");

// A check saved with every credential its form requires, as stored secrets
// read back.
const withCredential = (name, check) => {
  const config = { ...(check.config || {}) };
  (META[name].fields || [])
    .filter((f) => f.type === "password" && f.required)
    .forEach((f) => {
      config[f.key] = "__encrypted__0a1b2c3d";
    });
  return { ...check, config };
};

const renderTab = (saved, props = {}) => {
  const onChange = vi.fn();
  render(
    <GuardrailConfigTab
      guardrails={saved}
      saved={saved}
      onChange={onChange}
      {...props}
    />,
  );
  return onChange;
};

/** @returns {HTMLElement} */
const cardOf = (label) =>
  /** @type {HTMLElement} */ (
    screen
      .getByText(label, { selector: ".MuiTypography-subtitle2" })
      .closest(".MuiCard-root")
  );

const patchOf = (saved, draft) =>
  buildConfigPatch(
    { guardrails: saved },
    { guardrails: guardrailsForSave(draft, saved) },
    ["guardrails"],
  );

// MUI keeps the rest of the page hidden from assistive tech until a closing
// dialog's transition ends, so the next control is found only after that.
const dialogClosed = () =>
  waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

beforeEach(() => {
  mockOrgLevel = 8;
});

describe("a stored partner check this release does not offer is listed read-only", () => {
  it.each(PARTNERS)(
    "%s, saved off: its card, no switch, no Configure, no way to turn it on",
    (name) => {
      const label = META[name].label;
      renderTab({
        checks: { [name]: withCredential(name, { enabled: false }) },
      });

      expect(screen.getByText("Saved partner checks")).toBeInTheDocument();
      expect(screen.queryByText("AI-Powered Checks")).toBeNull();
      const card = cardOf(label);
      expect(within(card).getByText("Read-only")).toBeInTheDocument();
      expect(within(card).getByText("Off")).toBeInTheDocument();
      expect(within(card).queryByRole("checkbox")).toBeNull();
      expect(
        within(card).queryByRole("button", { name: /Configure/ }),
      ).toBeNull();
      expect(
        within(card).queryByRole("button", { name: /turn .*on|enable/i }),
      ).toBeNull();
      expect(
        within(card).queryByRole("button", { name: /Turn off/ }),
      ).toBeNull();
      expect(
        within(card).getByRole("button", { name: /^Remove/ }),
      ).toBeEnabled();
    },
  );

  it.each(PARTNERS)("%s, saved on: On, and Turn off", (name) => {
    renderTab({ checks: { [name]: withCredential(name, { enabled: true }) } });
    const card = cardOf(META[name].label);
    expect(within(card).getByText("On")).toBeInTheDocument();
    expect(
      within(card).getByRole("button", {
        name: `Turn off ${META[name].label}`,
      }),
    ).toBeEnabled();
    expect(within(card).queryByRole("checkbox")).toBeNull();
  });

  it("lists one saved without its credential too, saying so", () => {
    renderTab({
      rules: [
        {
          name: "llama-guard",
          enabled: true,
          action: "block",
          config: { endpoint: "https://guard.example.test/v1/chat" },
        },
      ],
    });
    const card = cardOf("Llama Guard");
    expect(within(card).getByText("On")).toBeInTheDocument();
    expect(within(card).getByText("API Key: not saved")).toBeInTheDocument();
    expect(
      within(card).getByText("Endpoint URL: guard.example.test"),
    ).toBeInTheDocument();
  });

  it("shows a credential only as saved, never its value", () => {
    const secret = "stored-clear-text-value-for-display-test";
    renderTab({
      rules: [
        {
          name: "zscaler-guard",
          enabled: false,
          config: { api_key: secret, tenant_id: "tenant-7" },
        },
      ],
    });
    const card = cardOf("Zscaler AI Guard");
    expect(within(card).getByText("API Key: saved")).toBeInTheDocument();
    expect(within(card).getByText("Tenant ID: tenant-7")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(secret);
  });

  it("850(a): keeps confirmed Turn off while the gateway is off, preserving all other stored values", () => {
    const saved = {
      checks: {
        "pangea-guard": withCredential("pangea-guard", { enabled: true }),
        "pii-detection": { enabled: true, action: "block" },
      },
    };
    const onChange = renderTab(saved, { nodePipeline: { enabled: false } });
    const card = cardOf("Pangea AI Guard");
    expect(within(card).getByText("Not enforced")).toBeInTheDocument();
    const turnOff = within(card).getByRole("button", { name: /Turn off/ });
    expect(turnOff).toBeEnabled();
    expect(within(card).getByRole("button", { name: /^Remove/ })).toBeEnabled();
    expect(within(card).queryByRole("checkbox")).toBeNull();
    expect(
      within(card).queryByRole("button", { name: /Configure/ }),
    ).toBeNull();
    fireEvent.click(turnOff);
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Turn off",
      }),
    );
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(patchOf(saved, onChange.mock.calls[0][0])).toEqual({
      guardrails: { checks: { "pangea-guard": { enabled: false } } },
    });
  });

  it("gives a Member the card with Turn off and Remove disabled", () => {
    mockOrgLevel = 3;
    renderTab({
      checks: {
        "lasso-guard": withCredential("lasso-guard", { enabled: true }),
      },
    });
    const card = cardOf("Lasso Security");
    expect(
      within(card).getByRole("button", { name: "Turn off Lasso Security" }),
    ).toBeDisabled();
    expect(
      within(card).getByRole("button", { name: /^Remove/ }),
    ).toBeDisabled();
  });

  // Positive control: a partner check the tab still offers keeps its
  // controls, and a fresh org lists no partner check at all.
  it("keeps Lakera Guard's switch and Configure, and lists nothing on a fresh org", () => {
    renderTab({
      checks: {
        "lakera-guard": withCredential("lakera-guard", { enabled: false }),
      },
    });
    const lakera = cardOf("Lakera Guard");
    expect(within(lakera).getByRole("checkbox")).toBeInTheDocument();
    expect(
      within(lakera).getByRole("button", { name: /Configure/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Saved partner checks")).toBeNull();
  });

  it("lists none and no heading on a fresh org", () => {
    renderTab({ checks: {} });
    expect(screen.queryByText("Saved partner checks")).toBeNull();
    for (const name of PARTNERS) {
      expect(screen.queryByText(META[name].label)).toBeNull();
    }
  });
});

describe("turning a stored partner check off or removing it is confirmed and changes only that check", () => {
  const LLAMA = {
    name: "llama-guard",
    stage: "pre",
    mode: "sync",
    action: "block",
    enabled: true,
    config: {
      endpoint: "https://guard.example.test/v1/chat",
      api_key: "__encrypted__",
      model: "meta-llama/Llama-Guard-3-8B",
    },
    _policy: "policy-llama",
  };
  const PII = {
    name: "pii-detector",
    stage: "pre",
    mode: "sync",
    action: "block",
    threshold: 0.7,
    enabled: true,
    config: {},
  };
  const RULES = { rules: [LLAMA, PII] };

  it("turns it off only once confirmed, and the save changes only its enabled", async () => {
    const onChange = renderTab(RULES);
    fireEvent.click(
      within(cardOf("Llama Guard")).getByRole("button", {
        name: "Turn off Llama Guard",
      }),
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(
      'Turn off "Llama Guard" in the draft? This release offers no way to turn it back on from the console. Click Save & Activate to apply.',
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await dialogClosed();
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.click(
      within(cardOf("Llama Guard")).getByRole("button", {
        name: "Turn off Llama Guard",
      }),
    );
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Turn off",
      }),
    );
    expect(onChange).toHaveBeenCalledTimes(1);
    const patch = patchOf(RULES, onChange.mock.calls[0][0]);
    expect(JSON.stringify(patch.guardrails.rules)).toBe(
      JSON.stringify([{ ...LLAMA, enabled: false }, PII]),
    );
  });

  it("removes it only once confirmed, and the save drops only it", async () => {
    const onChange = renderTab(RULES);
    const remove = () =>
      fireEvent.click(
        within(cardOf("Llama Guard")).getByRole("button", { name: /^Remove/ }),
      );
    remove();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(
      'Remove "Llama Guard" from the draft? Click Save & Activate to apply the removal.',
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await dialogClosed();
    expect(onChange).not.toHaveBeenCalled();

    remove();
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Remove",
      }),
    );
    expect(onChange).toHaveBeenCalledTimes(1);
    const patch = patchOf(RULES, onChange.mock.calls[0][0]);
    expect(patch.guardrails.rules).toHaveLength(1);
    expect(patch.guardrails.rules[0].name).toBe("pii-detector");
  });

  it("in a checks map, turning it off patches only its enabled, and removing it only nulls it", async () => {
    const saved = {
      checks: {
        "presidio-pii": {
          enabled: true,
          action: "block",
          config: {
            provider: "presidio",
            endpoint: "http://presidio.test:5001",
          },
        },
        "pii-detection": { enabled: true, action: "mask" },
      },
    };
    const onChange = renderTab(saved);
    fireEvent.click(
      within(cardOf("Presidio PII")).getByRole("button", {
        name: "Turn off Presidio PII",
      }),
    );
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Turn off",
      }),
    );
    expect(patchOf(saved, onChange.mock.calls[0][0])).toEqual({
      guardrails: { checks: { "presidio-pii": { enabled: false } } },
    });
    await dialogClosed();

    fireEvent.click(
      within(cardOf("Presidio PII")).getByRole("button", { name: /^Remove/ }),
    );
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Remove",
      }),
    );
    expect(patchOf(saved, onChange.mock.calls[1][0])).toEqual({
      guardrails: { checks: { "presidio-pii": null } },
    });
  });
});

// Saving any other change on the tab sends each stored-only check exactly as
// it is stored. A section stored as rules (or as the policy sync's checks
// list) goes back whole, so the rule itself must come back unchanged: no
// threshold, stage or mode the editor would fill in, no key dropped.
describe("a save of another check keeps every stored-only check byte for byte", () => {
  // The tool permissions rule as an org saved it through the API, with no
  // threshold; a partner rule with a key the editor knows nothing of.
  const TOOL = {
    name: "tool-permissions",
    stage: "pre",
    mode: "sync",
    action: "block",
    enabled: true,
    config: { mode: "denylist", tools: "sk_drop_table" },
  };
  const AZURE = {
    name: "azure-content-safety",
    enabled: false,
    action: "warn",
    config: {
      endpoint: "https://res.example.test",
      api_key: "__encrypted__",
      severity_threshold: 4,
    },
    note: "kept by the platform",
    _policy: "policy-azure",
  };
  const PII = {
    name: "pii-detector",
    stage: "pre",
    mode: "sync",
    action: "block",
    threshold: 0.7,
    enabled: false,
    config: {},
  };

  const togglePii = (onChange) => {
    fireEvent.click(within(cardOf("PII Detection")).getByRole("checkbox"));
    return onChange.mock.calls[0][0];
  };

  it("in a section stored as rules", () => {
    const saved = { rules: [TOOL, AZURE, PII], timeout_ms: 2000 };
    const onChange = renderTab(saved);
    const patch = patchOf(saved, togglePii(onChange));

    const sent = Object.fromEntries(
      patch.guardrails.rules.map((r) => [r.name, JSON.stringify(r)]),
    );
    expect(sent["tool-permissions"]).toBe(JSON.stringify(TOOL));
    expect(sent["azure-content-safety"]).toBe(JSON.stringify(AZURE));
    expect(JSON.parse(sent["pii-detector"]).enabled).toBe(true);
  });

  it("in a section the policy sync stored as a checks list", () => {
    const listTool = {
      name: "tool-permissions",
      enabled: true,
      action: "block",
      threshold: 0.8,
      config: { provider: "tool_permission", mode: "denylist", tools: "x_*" },
      _policy: "policy-tool",
    };
    const listAzure = { ...AZURE };
    const listPii = { ...PII };
    const saved = { checks: [listTool, listAzure, listPii] };
    const onChange = renderTab(saved);
    const patch = patchOf(saved, togglePii(onChange));

    // The list stays the policy sync's list (GL-1029-2): it is not turned
    // into rules, and every entry but the edited one goes back as stored.
    expect(patch.guardrails).not.toHaveProperty("rules");
    const sent = Object.fromEntries(
      patch.guardrails.checks.map((r) => [r.name, JSON.stringify(r)]),
    );
    expect(sent["tool-permissions"]).toBe(JSON.stringify(listTool));
    expect(sent["azure-content-safety"]).toBe(JSON.stringify(listAzure));
    expect(JSON.parse(sent["pii-detector"]).enabled).toBe(true);
  });

  it("in a section stored as a checks map, the patch carries the edited check alone", () => {
    const saved = {
      checks: {
        "tool-permissions": {
          enabled: true,
          action: "block",
          config: { provider: "tool_permission", mode: "denylist", tools: "x" },
        },
        "azure-content-safety": {
          enabled: false,
          config: {
            provider: "azure_content_safety",
            api_key: "__encrypted__",
          },
          _policy: "policy-azure",
        },
        "pii-detection": { enabled: false, action: "block" },
      },
    };
    const onChange = renderTab(saved);
    expect(patchOf(saved, togglePii(onChange))).toEqual({
      guardrails: { checks: { "pii-detection": { enabled: true } } },
    });
  });
});
