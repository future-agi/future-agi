// A stored partner or Tool Permissions check's read-only card on the Rules
// tab and in the Org config editor's Guardrails tab says On only when the
// gateway runs it, which is what the config push sends
// (futureagi/agentcc/services/config_push.py, _transform_guardrails, then
// the GatewayAdminContract dump with exclude_none):
// - a rules list or a policy-sync checks list: an entry is sent with its
//   enabled, true when it has none; one saved as null is sent without
//   enabled, which the gateway reads as off (Go's zero value) and skips. Of
//   two entries with one name the later one is sent;
// - a checks map: forwarded as stored, so an entry without enabled or with
//   null is off as well.
// An Off card offers no Turn off. The card only reads: nothing here changes
// the stored section, and a save sends a stored-only entry back as stored.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import GuardrailConfigTab, {
  MODEL_GUARDRAIL_CHECKS,
} from "./GuardrailConfigTab";
import OrgConfigEditor from "./OrgConfigEditor";
import { guardrailsForSave } from "../hooks/useConfigPatch";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
let level = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: level }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: {
    gateway: { updateConfig: (id) => `/gateways/${id}/update-config/` },
  },
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "synthetic-gateway" }),
}));
vi.mock("../providers/hooks/useGatewayConfig", async (original) => ({
  ...(await original()),
  useGatewayConfig: () => ({ data: {}, isLoading: false }),
}));

const META = Object.fromEntries(MODEL_GUARDRAIL_CHECKS.map((c) => [c.name, c]));
// The checks the console keeps only as stored (constants/storedOnlyChecks.js),
// listed here so this file also runs, and fails, against the tree before.
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

const PII = { name: "pii-detection", enabled: true, action: "block" };
const entry = (name, fields) => ({
  name,
  action: "block",
  config: { endpoint: "https://guard.example.test/v1" },
  ...fields,
});
const shapeOf = (shape, entries) =>
  shape === "rules"
    ? { rules: entries }
    : shape === "list"
      ? { checks: entries }
      : {
          checks: Object.fromEntries(
            entries.map(({ name, ...rest }) => [name, rest]),
          ),
        };
const cardOf = (name) => {
  const cards = document.querySelectorAll(`[data-stored-only-check="${name}"]`);
  expect(cards).toHaveLength(1);
  return within(/** @type {HTMLElement} */ (cards[0]));
};
const expectState = (view, name, on) => {
  expect(view.getByText(on ? "On" : "Off")).toBeInTheDocument();
  expect(view.queryByText(on ? "Off" : "On")).toBeNull();
  const turnOff = view.queryByRole("button", {
    name: `Turn off ${META[name].label}`,
  });
  if (on) expect(turnOff).toBeEnabled();
  else expect(turnOff).toBeNull();
  // Remove stays either way.
  expect(
    view.getByRole("button", { name: `Remove ${META[name].label}` }),
  ).toBeEnabled();
};

function renderTab(saved) {
  const onChange = vi.fn();
  const view = render(
    <GuardrailConfigTab guardrails={saved} saved={saved} onChange={onChange} />,
  );
  return { onChange, view };
}
function renderOrgConfig(saved) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <OrgConfigEditor
        open
        onClose={vi.fn()}
        initialConfig={{ guardrails: saved }}
        defaultTab={1}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  level = 8;
});

describe("a stored-only check saved with a null enabled reads Off, as the gateway skips it", () => {
  it.each(
    ["rules", "list", "map"].flatMap((shape) =>
      STORED_ONLY_CHECKS.map((name) => [shape, name]),
    ),
  )("%s: %s", (shape, name) => {
    const saved = shapeOf(shape, [entry(name, { enabled: null }), PII]);
    const before = JSON.stringify(saved);
    renderTab(saved);
    expectState(cardOf(name), name, false);
    expect(JSON.stringify(saved)).toBe(before);
  });
});

describe("absent, true, false and null side by side", () => {
  it.each([
    // The push gives a rule or list entry without enabled true.
    ["rules", {}, true],
    ["rules", { enabled: true }, true],
    ["rules", { enabled: false }, false],
    ["rules", { enabled: null }, false],
    ["list", {}, true],
    ["list", { enabled: true }, true],
    ["list", { enabled: false }, false],
    ["list", { enabled: null }, false],
    // A map is forwarded as stored: without enabled the gateway reads off.
    ["map", {}, false],
    ["map", { enabled: true }, true],
    ["map", { enabled: false }, false],
    ["map", { enabled: null }, false],
  ])("%s %j: On %s", (shape, fields, on) => {
    const saved = shapeOf(shape, [entry("llama-guard", fields), PII]);
    const before = JSON.stringify(saved);
    renderTab(saved);
    expectState(cardOf("llama-guard"), "llama-guard", on);
    expect(JSON.stringify(saved)).toBe(before);
  });
});

describe("a name saved twice reads as its later entry, the one the push sends", () => {
  it.each([
    ["rules", true, null, false],
    ["rules", null, true, true],
    ["rules", undefined, null, false],
    ["rules", null, undefined, true],
    ["list", true, null, false],
    ["list", null, true, true],
    ["list", false, null, false],
    ["list", null, undefined, true],
  ])("%s: enabled %j, then %j: On %s", (shape, first, later, on) => {
    const saved = shapeOf(shape, [
      entry("presidio-pii", { enabled: first, action: "log" }),
      PII,
      entry("presidio-pii", { enabled: later, action: "warn" }),
    ]);
    const before = JSON.stringify(saved);
    renderTab(saved);
    const view = cardOf("presidio-pii");
    expectState(view, "presidio-pii", on);
    // The chip and the action come from the same entry.
    expect(view.getByText("Action: warn")).toBeInTheDocument();
    expect(JSON.stringify(saved)).toBe(before);
  });
});

describe("an edited draft does not turn a stored null on", () => {
  it.each(["rules", "list"])(
    "%s: after another edit the card still reads Off, and the save keeps the null",
    (shape) => {
      const saved = shapeOf(shape, [
        entry("tool-permissions", {
          enabled: null,
          config: { provider: "tool_permission", mode: "denylist", tools: [] },
        }),
        PII,
      ]);
      const before = JSON.stringify(saved);
      const { onChange, view } = renderTab(saved);
      fireEvent.change(screen.getByLabelText("Timeout (ms)"), {
        target: { value: "7000" },
      });
      expect(onChange).toHaveBeenCalledTimes(1);
      const draft = onChange.mock.calls[0][0];
      expect(draft.timeout_ms).toBe(7000);
      view.rerender(
        <GuardrailConfigTab
          guardrails={draft}
          saved={saved}
          onChange={onChange}
        />,
      );
      expectState(cardOf("tool-permissions"), "tool-permissions", false);
      // What a save sends back for it is the stored entry, null and all, so
      // the gateway goes on skipping it.
      const toSave = guardrailsForSave(draft, saved);
      const rules = toSave[shape === "rules" ? "rules" : "checks"];
      expect(rules.filter((r) => r.name === "tool-permissions")).toEqual([
        saved[shape === "rules" ? "rules" : "checks"][0],
      ]);
      expect(JSON.stringify(saved)).toBe(before);
    },
  );

  it("a stored on check turned off in the draft reads Off", async () => {
    const saved = shapeOf("rules", [
      entry("llama-guard", { enabled: true }),
      PII,
    ]);
    const { onChange, view } = renderTab(saved);
    fireEvent.click(
      cardOf("llama-guard").getByRole("button", {
        name: "Turn off Llama Guard",
      }),
    );
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Turn off",
      }),
    );
    expect(onChange).toHaveBeenCalledTimes(1);
    // MUI keeps the rest of the page hidden from assistive tech until the
    // closing dialog's transition ends.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    view.rerender(
      <GuardrailConfigTab
        guardrails={onChange.mock.calls[0][0]}
        saved={saved}
        onChange={onChange}
      />,
    );
    expectState(cardOf("llama-guard"), "llama-guard", false);
  });
});

describe("the Org config editor's Guardrails tab reads the same state", () => {
  it.each([
    ["rules", { enabled: null }, false],
    ["list", { enabled: null }, false],
    ["map", { enabled: null }, false],
    ["rules", {}, true],
    ["list", {}, true],
    ["map", {}, false],
  ])("%s %j: On %s", (shape, fields, on) => {
    const saved = shapeOf(shape, [entry("zscaler-guard", fields), PII]);
    const before = JSON.stringify(saved);
    renderOrgConfig(saved);
    expectState(cardOf("zscaler-guard"), "zscaler-guard", on);
    expect(JSON.stringify(saved)).toBe(before);
  });
});
