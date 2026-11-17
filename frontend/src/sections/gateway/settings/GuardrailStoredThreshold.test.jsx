// A stored partner check's card on the Rules tab and in the Org config
// editor's Guardrails tab shows the threshold the gateway runs it at, which
// is what the config push sends (futureagi/agentcc/services/config_push.py,
// _transform_guardrails, then the GatewayAdminContract dump with
// exclude_none):
// - a rules list or a policy-sync checks list: the later of two entries with
//   one name is sent (the editor keeps that one too); an entry without a
//   threshold is sent at 0.8; one saved with a null threshold is sent
//   without one, which the gateway runs as 0 (Go's zero value);
// - a checks map: forwarded as stored; confidence_threshold, when saved
//   (even as null), wins over confidenceThreshold; null or none runs as 0.
// The card only reads; nothing here changes the stored section.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, within } from "src/utils/test-utils";
import GuardrailConfigTab from "./GuardrailConfigTab";
import OrgConfigEditor from "./OrgConfigEditor";

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

const LLAMA = { name: "llama-guard", enabled: true };
const PII = { name: "pii-detection", enabled: false, action: "block" };
const shapeOf = (shape, entries) =>
  shape === "rules"
    ? { rules: entries }
    : shape === "list"
      ? { checks: entries }
      : {
          checks: Object.fromEntries(
            entries.map(({ name, ...entry }) => [name, entry]),
          ),
        };
const cards = (name) =>
  document.querySelectorAll(`[data-stored-only-check="${name}"]`);
const llamaCard = () => {
  expect(cards("llama-guard")).toHaveLength(1);
  return within(/** @type {HTMLElement} */ (cards("llama-guard")[0]));
};
const expectThreshold = (view, text, warns) => {
  expect(view.getByText(`Threshold: ${text}`)).toBeInTheDocument();
  expect(view.queryAllByText(/^Threshold: /)).toHaveLength(1);
  if (warns) expect(view.getByText(/never triggers/)).toBeInTheDocument();
  else expect(view.queryByText(/never triggers/)).toBeNull();
};

function renderTab(saved) {
  render(
    <GuardrailConfigTab guardrails={saved} saved={saved} onChange={vi.fn()} />,
  );
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

// A JSON object keeps one value per key, the later one (as Python's json and
// Postgres jsonb do), so a checks map holds a name once.
const MAP_SAVED_TWICE =
  '{"checks":{"llama-guard":{"enabled":true,"action":"log","confidence_threshold":0.31},' +
  '"pii-detection":{"enabled":false},' +
  '"llama-guard":{"enabled":true,"action":"warn","confidence_threshold":1}}}';

describe("a name saved twice shows the later entry, the one the push sends", () => {
  it.each([
    ["rules", 0.31, 1, "1.00", true],
    ["rules", 1, 0.31, "0.31", false],
    ["rules", 1, null, "0.00", false],
    ["list", 0.31, 1, "1.00", true],
    ["list", 1, 0.31, "0.31", false],
    ["list", 0.5, null, "0.00", false],
  ])(
    "%s: threshold %j, then %j, shows %s",
    (shape, first, later, expected, warns) => {
      const saved = shapeOf(shape, [
        { ...LLAMA, action: "log", threshold: first },
        PII,
        { ...LLAMA, action: "warn", threshold: later },
      ]);
      const before = JSON.stringify(saved);
      renderTab(saved);
      const view = llamaCard();
      expectThreshold(view, expected, warns);
      // The action and the threshold come from the same entry.
      expect(view.getByText("Action: warn")).toBeInTheDocument();
      expect(JSON.stringify(saved)).toBe(before);
    },
  );

  it("map: a key saved twice is held once, the later, and shown as such", () => {
    const saved = JSON.parse(MAP_SAVED_TWICE);
    expect(Object.keys(saved.checks)).toEqual(["llama-guard", "pii-detection"]);
    expect(saved.checks["llama-guard"].confidence_threshold).toBe(1);
    renderTab(saved);
    const view = llamaCard();
    expectThreshold(view, "1.00", true);
    expect(view.getByText("Action: warn")).toBeInTheDocument();
  });
});

describe("a threshold saved as null shows the 0.00 the gateway runs it at", () => {
  it.each([
    // The push reads a rule's threshold only, never its confidence_threshold.
    ["rules", { threshold: null }],
    ["rules", { threshold: null, confidence_threshold: 1 }],
    ["list", { threshold: null }],
    ["list", { threshold: null, confidence_threshold: 0.9 }],
    // A saved confidence_threshold key is read first, even as null.
    ["map", { confidence_threshold: null, confidenceThreshold: 1 }],
    ["map", { confidence_threshold: null, confidenceThreshold: 0.45 }],
  ])("%s %j", (shape, fields) => {
    const saved = shapeOf(shape, [{ ...LLAMA, ...fields }, PII]);
    const before = JSON.stringify(saved);
    renderTab(saved);
    expectThreshold(llamaCard(), "0.00", false);
    expect(JSON.stringify(saved)).toBe(before);
  });
});

describe("missing, null and zero thresholds side by side", () => {
  it.each([
    ["rules", {}, "0.80"],
    ["rules", { threshold: null }, "0.00"],
    ["rules", { threshold: 0 }, "0.00"],
    ["list", {}, "0.80"],
    ["list", { threshold: null }, "0.00"],
    ["list", { threshold: 0 }, "0.00"],
    ["map", {}, "0.00"],
    ["map", { confidence_threshold: null }, "0.00"],
    ["map", { confidenceThreshold: null }, "0.00"],
    ["map", { confidence_threshold: 0 }, "0.00"],
    ["map", { confidenceThreshold: 0.57 }, "0.57"],
  ])("%s %j shows %s", (shape, fields, expected) => {
    renderTab(shapeOf(shape, [{ ...LLAMA, ...fields }]));
    expectThreshold(llamaCard(), expected, false);
  });
});

describe("the Org config editor's Guardrails tab shows the same threshold", () => {
  it.each([
    [
      "rules, a name saved twice",
      shapeOf("rules", [
        { ...LLAMA, threshold: 0.31 },
        { ...LLAMA, threshold: 1 },
      ]),
      "1.00",
      true,
    ],
    [
      "list, a name saved twice",
      shapeOf("list", [
        { ...LLAMA, threshold: 0.31 },
        { ...LLAMA, threshold: 1 },
      ]),
      "1.00",
      true,
    ],
    ["map, a key saved twice", JSON.parse(MAP_SAVED_TWICE), "1.00", true],
    [
      "rules, a null threshold",
      shapeOf("rules", [{ ...LLAMA, threshold: null }]),
      "0.00",
      false,
    ],
    [
      "list, a null threshold",
      shapeOf("list", [{ ...LLAMA, threshold: null }]),
      "0.00",
      false,
    ],
    [
      "map, a null confidence_threshold beside a confidenceThreshold",
      shapeOf("map", [
        { ...LLAMA, confidence_threshold: null, confidenceThreshold: 1 },
      ]),
      "0.00",
      false,
    ],
  ])("%s", (_case, saved, expected, warns) => {
    const before = JSON.stringify(saved);
    renderOrgConfig(saved);
    expectThreshold(llamaCard(), expected, warns);
    expect(JSON.stringify(saved)).toBe(before);
  });
});
