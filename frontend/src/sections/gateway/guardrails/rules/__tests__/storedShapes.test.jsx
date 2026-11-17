import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  DEMO_FEEDBACK_SUMMARY,
  DEMO_OVERVIEW,
  DEMO_RULE_STATS,
  MockAgGrid,
  renderPage,
} from "../../__tests__/helpers";
import RulesView from "../RulesView";

const { get, post, snack } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  snack: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get, post } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: snack }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

// A section the guardrail policy sync stored as a checks list: an MCP
// Security check turned off, with untrimmed and unknown settings and a null
// threshold, and a PII check saved with a null threshold, which the config
// push sends as none and the gateway runs at 0.
const MCP_ENTRY = {
  name: "mcp-security",
  enabled: false,
  threshold: null,
  future_rule_key: " untouched ",
  config: {
    // Closed boolean values stay typed; the text and unknown settings
    // below retain the legacy untrimmed preservation coverage.
    validate_inputs: true,
    blocked_tools: " db_write ",
    custom_patterns: ["LIST_MARKER"],
    future_setting: { keep: " exact " },
  },
  _policy: "policy-mcp",
};
// A graded built-in check stored off, with a null threshold and keys the
// form never shows. The base lists it in Rules (it lists no stored MCP
// Security check, which has no account field: isShownCheck), so the
// drawer's list-entry saves are exercised through it.
const INJECTION_ENTRY = {
  name: "injection-detector",
  enabled: false,
  threshold: null,
  future_rule_key: " untouched ",
  config: { future_setting: { keep: " exact " } },
  _policy: "policy-injection",
};
const PII_ENTRY = {
  name: "pii-detector",
  enabled: true,
  action: "block",
  threshold: null,
  config: {},
  _policy: "policy-pii",
};

let guardrails;

function answer(url) {
  if (url.endsWith("/gateways/gw-1/config/")) {
    return Promise.resolve({
      data: {
        result: {
          guardrail_pipeline: {
            enabled: true,
            fail_open: false,
            mode: "sequential",
          },
        },
      },
    });
  }
  if (url.includes("org-configs/active")) {
    return Promise.resolve({ data: { result: { guardrails } } });
  }
  if (url.includes("guardrail-rules")) {
    return Promise.resolve({ data: { result: DEMO_RULE_STATS } });
  }
  if (url.includes("guardrail-overview")) {
    return Promise.resolve({ data: { result: DEMO_OVERVIEW } });
  }
  if (url.includes("guardrail-feedback/summary")) {
    return Promise.resolve({ data: { result: DEMO_FEEDBACK_SUMMARY } });
  }
  return Promise.reject(new Error(`unexpected GET ${url}`));
}

const row = (id) => screen.findByTestId(`row-${id}`);

async function openRule(user, id, label) {
  await user.click(within(await row(id)).getByText(label));
  return screen.findByRole("dialog", { name: label });
}

beforeEach(() => {
  guardrails = structuredClone({
    checks: [MCP_ENTRY, INJECTION_ENTRY, PII_ENTRY],
  });
  get.mockReset();
  post.mockReset();
  snack.mockReset();
  get.mockImplementation(answer);
  post.mockResolvedValue({
    data: { result: { version: 3, gateway_synced: true } },
  });
});

describe("a check of a section stored as the policy sync's checks list", () => {
  it("re-enables through the drawer by sending only enabled", async () => {
    const user = userEvent.setup();
    renderPage(<RulesView gatewayId="gw-1" />);
    const drawer = await openRule(user, "prompt-injection", "Prompt Injection");
    await user.click(within(drawer).getByRole("checkbox", { name: "Off" }));
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/update-guardrail/",
    );
    expect(post.mock.calls[0][1]).toEqual({
      name: "injection-detector",
      config: { enabled: true },
    });
  });

  it("sends nothing for a drawer opened and saved unchanged", async () => {
    const user = userEvent.setup();
    renderPage(<RulesView gatewayId="gw-1" />);
    const drawer = await openRule(user, "prompt-injection", "Prompt Injection");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(snack).toHaveBeenCalledWith("No changes to save", {
        variant: "info",
      }),
    );
    expect(post).not.toHaveBeenCalled();
  });

  it("shows a null threshold as 0, the threshold the gateway runs", async () => {
    const user = userEvent.setup();
    renderPage(<RulesView gatewayId="gw-1" />);
    expect(
      within(await row("prompt-injection")).getByText("0.00"),
    ).toBeInTheDocument();
    const drawer = await openRule(user, "prompt-injection", "Prompt Injection");
    expect(
      within(drawer).getByText("Match threshold: 0.00"),
    ).toBeInTheDocument();
  });
});
