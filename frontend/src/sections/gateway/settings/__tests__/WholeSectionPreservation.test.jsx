/* eslint-disable react/prop-types */
import React, { forwardRef, useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import {
  DEMO_FEEDBACK_SUMMARY,
  DEMO_OVERVIEW,
  DEMO_RULE_STATS,
  MockAgGrid,
  renderPage,
} from "../../guardrails/__tests__/helpers";
import RulesView from "../../guardrails/rules/RulesView";
import GuardrailConfigTab from "../GuardrailConfigTab";
import {
  buildConfigPatch,
  guardrailsForSave,
  useConfigPatch,
} from "../../hooks/useConfigPatch";

const { get, post, snack } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  snack: vi.fn(),
}));
vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return { ...actual, default: { ...actual.default, get, post } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: snack }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

const SHAPES = ["rules", "checks-list", "checks-map"];
const OTHER = {
  name: "keyword-blocklist",
  enabled: true,
  action: "block",
  config: { words: ["fixture"] },
};
const VARIANTS = [
  { enabled: null, action: "block", threshold: null },
  { enabled: true, action: null, threshold: 0 },
  {},
  {
    enabled: false,
    threshold: null,
    confidence_threshold: 1,
    confidenceThreshold: 0.4,
    provider: null,
    future_top: { keep: [null, " exact "] },
  },
];
const clone = (v) => structuredClone(v);
function section(shape, entries) {
  if (shape === "checks-map") {
    return {
      checks: Object.fromEntries(
        entries.map(({ name, ...entry }) => [name, clone(entry)]),
      ),
    };
  }
  return { [shape === "rules" ? "rules" : "checks"]: clone(entries) };
}
// The update-config endpoint's recursive merge-patch semantics. Arrays are
// replaced wholesale; comparing the result catches edits to untouched bytes.
function applyPatch(saved, patch) {
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) return patch;
  const next = { ...saved };
  Object.entries(patch).forEach(([key, value]) => {
    if (value === null) delete next[key];
    else next[key] = applyPatch(next[key], value);
  });
  return next;
}
let stored;
function OrgEditor() {
  const [draft, setDraft] = useState(stored);
  const save = useConfigPatch();
  return (
    <>
      <GuardrailConfigTab
        guardrails={draft}
        saved={stored}
        onChange={setDraft}
        nodePipeline={{ enabled: true, mode: "sequential", fail_open: false }}
      />
      <button
        onClick={() =>
          save.mutate({
            gatewayId: "gw-1",
            saved: { guardrails: stored },
            patch: buildConfigPatch(
              { guardrails: stored },
              { guardrails: guardrailsForSave(draft, stored) },
              ["guardrails"],
            ),
          })
        }
      >
        Save section
      </button>
    </>
  );
}
const card = (label) =>
  screen
    .getAllByText(label)
    .map((node) => node.closest(".MuiCard-root"))
    .find(Boolean);
async function orgDialog(label) {
  fireEvent.click(within(card(label)).getAllByRole("button")[0]);
  return screen.findByRole("dialog");
}
// The base editor's Remove (MCP-LINE-2's card said Reset): it asks first,
// then drops the check from the draft (GuardrailConfigTab handleResetCheck).
function removeFromDraft(label) {
  fireEvent.click(within(card(label)).getByRole("button", { name: "Remove" }));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "Remove" }),
  );
}
async function rulesDrawer(id, label) {
  const row = await screen.findByTestId(`row-${id}`);
  fireEvent.click(within(row).getByText(label));
  return screen.findByRole("dialog", { name: label });
}
async function postedSection() {
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][0]).toBe("/agentcc/gateways/gw-1/update-config/");
  return applyPatch(stored, post.mock.calls[0][1].guardrails);
}
beforeEach(() => {
  get.mockReset();
  post.mockReset();
  snack.mockReset();
  get.mockImplementation(async (url) => {
    if (url.endsWith("/gateways/gw-1/config/"))
      return { data: { result: { guardrail_pipeline: { enabled: true } } } };
    if (url.includes("org-configs/active"))
      return { data: { result: { guardrails: clone(stored) } } };
    if (url.includes("guardrail-rules"))
      return { data: { result: DEMO_RULE_STATS } };
    if (url.includes("guardrail-overview"))
      return { data: { result: DEMO_OVERVIEW } };
    if (url.includes("guardrail-feedback/summary"))
      return { data: { result: DEMO_FEEDBACK_SUMMARY } };
    throw new Error(`Unexpected GET ${url}`);
  });
  post.mockResolvedValue({
    data: { result: { version: 4, gateway_synced: true } },
  });
});

describe.each(SHAPES)("whole-section preservation: %s", (shape) => {
  describe.each(VARIANTS)("untouched stored fields %j", (fields) => {
    const entries = () => [
      {
        name: "mcp-security",
        ...fields,
        config: { blocked_tools: " lookup ", future_config: [null, " exact "] },
      },
      clone(OTHER),
    ];
    it.each(["toggle", "dialog", "reset"])(
      "Org Config %s changes only the selected check",
      async (operation) => {
        const original = entries();
        stored = section(shape, original);
        const before = clone(stored);
        renderPage(<OrgEditor />);
        if (operation === "toggle") {
          fireEvent.click(
            within(card("Keyword Blocklist")).getByRole("checkbox"),
          );
          original[1].enabled = false;
        } else if (operation === "dialog") {
          const dialog = await orgDialog("Keyword Blocklist");
          fireEvent.change(within(dialog).getByLabelText(/Blocked Keywords/), {
            target: { value: "changed" },
          });
          fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
          original[1].config.words = ["changed"];
        } else {
          removeFromDraft("Keyword Blocklist");
          original.pop();
        }
        fireEvent.click(
          await screen.findByRole("button", { name: "Save section" }),
        );
        expect(JSON.stringify(await postedSection())).toBe(
          JSON.stringify(section(shape, original)),
        );
        expect(stored).toEqual(before);
      },
    );
    it("Rules Remove confirms and preserves every other stored byte", async () => {
      const original = entries();
      stored = section(shape, original);
      renderPage(<RulesView gatewayId="gw-1" />);
      const drawer = await rulesDrawer(
        "keyword-blocklist",
        "Keyword Blocklist",
      );
      fireEvent.click(within(drawer).getByRole("button", { name: "Remove" }));
      expect(post).not.toHaveBeenCalled();
      fireEvent.click(within(drawer).getByRole("button", { name: "Remove" }));
      original.pop();
      expect(JSON.stringify(await postedSection())).toBe(
        JSON.stringify(section(shape, original)),
      );
    });
  });
});

describe.each(["rules", "checks-list"])(
  "duplicate preservation: %s",
  (shape) => {
    it.each(["toggle", "reset"])(
      "%s another check retains duplicate and alias entries in order",
      async (operation) => {
        const original = [
          { name: "pii-detector", enabled: null, threshold: null },
          { name: "pii-detection", enabled: true, action: null },
          { name: "mcp-security", enabled: true, threshold: 1 },
          {
            name: "mcp-security",
            enabled: false,
            config: { blocked_tools: "lookup" },
          },
          clone(OTHER),
        ];
        stored = section(shape, original);
        renderPage(<OrgEditor />);
        if (operation === "toggle") {
          fireEvent.click(
            within(card("Keyword Blocklist")).getByRole("checkbox"),
          );
          original.at(-1).enabled = false;
        } else {
          removeFromDraft("Keyword Blocklist");
          original.pop();
        }
        fireEvent.click(
          await screen.findByRole("button", { name: "Save section" }),
        );
        expect(JSON.stringify(await postedSection())).toBe(
          JSON.stringify(section(shape, original)),
        );
      },
    );
  },
);
