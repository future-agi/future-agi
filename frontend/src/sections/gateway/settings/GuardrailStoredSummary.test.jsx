import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  fireEvent,
  within,
  waitFor,
} from "src/utils/test-utils";
import GuardrailConfigTab from "./GuardrailConfigTab";
import OrgConfigEditor from "./OrgConfigEditor";
import { buildConfigPatch, guardrailsForSave } from "../hooks/useConfigPatch";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
let level = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: level }),
}));
const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { post, get: vi.fn() },
  endpoints: {
    gateway: { updateConfig: (id) => `/gateways/${id}/update-config/` },
  },
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "synthetic-gateway" }),
}));
vi.mock("../providers/hooks/useGatewayConfig", async (original) => ({
  ...(await /** @type {() => Promise<typeof import("../providers/hooks/useGatewayConfig")>} */ (
    original
  )()),
  useGatewayConfig: () => ({ data: {}, isLoading: false }),
}));

const TOOL = {
  name: "tool-permissions",
  enabled: true,
  action: "block",
  stage: "pre",
  mode: "sync",
  threshold: 0.73,
  config: {
    provider: "tool_permission",
    mode: "denylist",
    tools: "db_*, file_write",
  },
  _policy: "synthetic-policy",
  opaque: { keep: [1, 2] },
};
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
const entriesOf = (shape, saved) =>
  shape === "rules" ? saved.rules : saved.checks;
const card = (name) =>
  /** @type {HTMLElement} */ (
    document.querySelector(`[data-stored-only-check="${name}"]`)
  );
const patchOf = (saved, draft) =>
  buildConfigPatch(
    { guardrails: saved },
    { guardrails: guardrailsForSave(draft, saved) },
    ["guardrails"],
  );
function renderTab(saved) {
  const onChange = vi.fn();
  render(
    <GuardrailConfigTab guardrails={saved} saved={saved} onChange={onChange} />,
  );
  return onChange;
}
const closeDialog = () =>
  waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
const togglePii = () =>
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Turn PII Detection on or off" }),
  );

beforeEach(() => {
  level = 8;
  post.mockReset();
  post.mockResolvedValue({
    data: { result: { version: 2, gateway_synced: true } },
  });
});

describe("stored URL summaries do not disclose credential markers", () => {
  it.each(["", " ", "\t", "\r\n", " "])(
    "renders only the host for whitespace prefix %j without rewriting bytes",
    (prefix) => {
      const marker = "synthetic-hidden-marker";
      const endpoint = `${prefix}HTTPS://user:${marker}@partner.invalid:8443/${marker}?token=${marker}#${marker}`;
      const saved = {
        rules: [
          {
            name: "llama-guard",
            enabled: true,
            threshold: 0.31,
            config: { endpoint },
          },
          PII,
        ],
      };
      const before = JSON.stringify(saved);
      const change = renderTab(saved);
      expect(
        within(card("llama-guard")).getByText(
          "Endpoint URL: partner.invalid:8443",
        ),
      ).toBeInTheDocument();
      expect(document.body.innerHTML).not.toContain(marker);
      togglePii();
      expect(
        JSON.stringify(
          patchOf(saved, change.mock.calls[0][0]).guardrails.rules[0],
        ),
      ).toBe(JSON.stringify(saved.rules[0]));
      expect(JSON.stringify(saved)).toBe(before);
    },
  );

  it.each([
    "\u0000https://u:synthetic-hidden-marker@partner.invalid/path",
    "ht\ttps://u:synthetic-hidden-marker@partner.invalid/path",
    "//u:synthetic-hidden-marker@partner.invalid/path",
    "https:///synthetic-hidden-marker",
    "https://[invalid]/synthetic-hidden-marker",
    "not a URL synthetic-hidden-marker",
    ["https://u:synthetic-hidden-marker@partner.invalid/path"],
  ])("hides ambiguous URL input %j rather than echoing it", (endpoint) => {
    renderTab({ rules: [{ name: "llama-guard", config: { endpoint } }] });
    expect(
      within(card("llama-guard")).getByText(
        "Endpoint URL: saved URL (details hidden)",
      ),
    ).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("synthetic-hidden-marker");
  });

  it("applies host-only rendering to base_url too", () => {
    renderTab({
      rules: [
        {
          name: "crowdstrike-aidr",
          config: {
            base_url:
              " \thttp://u:synthetic-hidden-marker@partner.invalid/private",
          },
        },
      ],
    });
    expect(
      within(card("crowdstrike-aidr")).getByText("Base URL: partner.invalid"),
    ).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("synthetic-hidden-marker");
  });
});

describe.each(["rules", "list", "map"])(
  "stored Tool Permissions in %s",
  (shape) => {
    it("shows mode/tools read-only, keeps an unrelated save byte-identical", () => {
      const saved = shapeOf(shape, [TOOL, PII]);
      const before = JSON.stringify(saved);
      const change = renderTab(saved);
      const view = within(card("tool-permissions"));
      expect(view.getByText("Read-only")).toBeInTheDocument();
      expect(view.getByText("Mode: denylist")).toBeInTheDocument();
      expect(
        view.getByText("Tool Patterns: db_*, file_write"),
      ).toBeInTheDocument();
      expect(view.queryByRole("checkbox")).toBeNull();
      expect(
        view.queryByRole("button", { name: /configure|edit|enable|turn on/i }),
      ).toBeNull();
      togglePii();
      const patch = patchOf(saved, change.mock.calls[0][0]);
      if (shape === "map")
        expect(patch).toEqual({
          guardrails: { checks: { "pii-detection": { enabled: true } } },
        });
      else
        expect(JSON.stringify(entriesOf(shape, patch.guardrails)[0])).toBe(
          JSON.stringify(entriesOf(shape, saved)[0]),
        );
      expect(JSON.stringify(saved)).toBe(before);
    });

    it.each([15, 8])(
      "role %s can confirm disable and remove without changing another stored entry",
      async (role) => {
        level = role;
        const partner = {
          name: "llama-guard",
          enabled: true,
          config: { endpoint: "https://partner.invalid" },
          opaque: 42,
        };
        const saved = shapeOf(shape, [TOOL, partner]);
        const before = JSON.stringify(saved);
        const change = renderTab(saved);
        for (const action of ["Turn off", "Remove"]) {
          const open = () =>
            fireEvent.click(
              within(card("tool-permissions")).getByRole("button", {
                name: `${action} Tool Permissions`,
              }),
            );
          open();
          fireEvent.click(
            within(screen.getByRole("dialog")).getByRole("button", {
              name: "Cancel",
            }),
          );
          await closeDialog();
          const count = change.mock.calls.length;
          open();
          fireEvent.click(
            within(screen.getByRole("dialog")).getByRole("button", {
              name: action,
            }),
          );
          expect(change).toHaveBeenCalledTimes(count + 1);
          const patch = patchOf(saved, change.mock.calls[count][0]);
          if (shape === "map")
            expect(patch).toEqual({
              guardrails: {
                checks: {
                  "tool-permissions":
                    action === "Remove" ? null : { enabled: false },
                },
              },
            });
          else
            expect(entriesOf(shape, patch.guardrails)).toEqual(
              action === "Remove"
                ? [partner]
                : [{ ...TOOL, enabled: false }, partner],
            );
          await closeDialog();
        }
        expect(JSON.stringify(saved)).toBe(before);
      },
    );

    it("lets a Member inspect settings but not disable/remove", () => {
      level = 3;
      renderTab(shapeOf(shape, [TOOL]));
      const view = within(card("tool-permissions"));
      expect(view.getByText("Mode: denylist")).toBeInTheDocument();
      expect(
        view.getByRole("button", { name: "Turn off Tool Permissions" }),
      ).toBeDisabled();
      expect(
        view.getByRole("button", { name: "Remove Tool Permissions" }),
      ).toBeDisabled();
    });

    it("shows a saved off policy without a turn-on path", () => {
      renderTab(shapeOf(shape, [{ ...TOOL, enabled: false }]));
      const view = within(card("tool-permissions"));
      expect(view.getByText("Off")).toBeInTheDocument();
      expect(view.queryByRole("checkbox")).toBeNull();
      expect(view.getAllByRole("button")).toHaveLength(1);
      expect(
        view.getByRole("button", { name: "Remove Tool Permissions" }),
      ).toBeEnabled();
    });

    it("Org Config Save & Activate sends an unrelated edit without rewriting the stored policy", async () => {
      const saved = shapeOf(shape, [TOOL, PII]);
      const before = JSON.stringify(saved);
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
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
      expect(
        within(card("tool-permissions")).getByText("Mode: denylist"),
      ).toBeInTheDocument();
      expect(
        within(card("tool-permissions")).getByText(
          "Tool Patterns: db_*, file_write",
        ),
      ).toBeInTheDocument();
      togglePii();
      fireEvent.click(screen.getByRole("button", { name: "Save & Activate" }));
      await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
      const patch = post.mock.calls[0][1];
      if (shape === "map")
        expect(patch).toEqual({
          guardrails: { checks: { "pii-detection": { enabled: true } } },
        });
      else
        expect(JSON.stringify(entriesOf(shape, patch.guardrails)[0])).toBe(
          JSON.stringify(entriesOf(shape, saved)[0]),
        );
      expect(JSON.stringify(saved)).toBe(before);
    });
  },
);

describe("stored threshold summaries match the original representation", () => {
  it.each([
    ["rules", { threshold: 0.23, confidence_threshold: 0.91 }, "0.23"],
    ["list", { threshold: 0.31, confidence_threshold: 0.92 }, "0.31"],
    ["map", { threshold: 0.42, confidence_threshold: 0.63 }, "0.63"],
    ["rules", { threshold: 0 }, "0.00"],
    ["list", { threshold: 0 }, "0.00"],
    ["map", {}, "0.00"],
    ["list", {}, "0.80"],
  ])("%s displays its own threshold %j", (shape, fields, expected) => {
    renderTab(
      shapeOf(shape, [{ name: "llama-guard", enabled: true, ...fields }]),
    );
    expect(
      within(card("llama-guard")).getByText(`Threshold: ${expected}`),
    ).toBeInTheDocument();
    expect(
      within(card("llama-guard")).queryByText(/never triggers/),
    ).toBeNull();
  });

  it.each(["rules", "list", "map"])(
    "%s warns for threshold 1, including hidden-confidence Tool Permissions",
    (shape) => {
      const fields =
        shape === "map" ? { confidence_threshold: 1 } : { threshold: 1 };
      renderTab(
        shapeOf(shape, [
          { ...TOOL, ...fields },
          { name: "llama-guard", enabled: true, ...fields },
        ]),
      );
      for (const name of ["llama-guard", "tool-permissions"]) {
        expect(
          within(card(name)).getByText("Threshold: 1.00"),
        ).toBeInTheDocument();
        expect(
          within(card(name)).getByText(/never triggers/),
        ).toBeInTheDocument();
      }
    },
  );
});
