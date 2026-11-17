import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
let orgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

// An organization's section as an older console stored it: an MCP Security
// rule and a Tool Permissions rule (both were offered then; neither is now:
// noToolOrMcpChecks.test.jsx), next to a rule of another kind. MCP
// Security's stored config carries an untrimmed Allowed Servers and keys the
// form never showed. Writes about the other rule must not touch them.
const MCP_SECURITY = {
  name: "mcp-security",
  stage: "pre",
  mode: "sync",
  action: "block",
  threshold: 0.5,
  enabled: true,
  config: {
    allowed_servers: " walk-echo ",
    blocked_tools: "db_write",
    validate_inputs: "true",
    max_calls_per_request: 4,
    custom_patterns: ["(?i)drop"],
    tool_rate_limits: { private_tool_name: 3, disabled_limit: 0 },
    provider: "mcp_security",
  },
};
// As this page's Add check saves a rule: no stage (the gateway never reads
// one).
const TOOL_PERMISSIONS = {
  name: "tool-permissions",
  mode: "sync",
  action: "block",
  threshold: 0.5,
  enabled: true,
  config: { mode: "denylist", tools: "file_*", provider: "tool_permission" },
};
const KEYWORDS = {
  name: "keyword-blocklist",
  stage: "pre",
  mode: "sync",
  action: "block",
  threshold: 0.8,
  enabled: true,
  config: { words: ["project-zeus"] },
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

const renderRules = () => renderPage(<RulesView gatewayId="gw-1" />);
const row = (id) => screen.findByTestId(`row-${id}`);

async function openRule(user, id, label) {
  await user.click(within(await row(id)).getByText(label));
  return screen.findByRole("dialog", { name: label });
}

beforeEach(() => {
  guardrails = structuredClone({
    rules: [MCP_SECURITY, TOOL_PERMISSIONS, KEYWORDS],
  });
  orgLevel = 8;
  get.mockReset();
  post.mockReset();
  snack.mockReset();
  get.mockImplementation(answer);
  post.mockResolvedValue({
    data: { result: { version: 3, gateway_synced: true } },
  });
});

// A write about something else sends nothing of the stored MCP rules, so
// what the gateway runs for them does not change.
describe("unrelated writes keep the stored MCP rules as they are", () => {
  it("switching another rule off sends only that rule's name and state", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(
      within(await row("keyword-blocklist")).getByRole("checkbox", {
        name: "Keyword Blocklist on",
      }),
    );
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toEqual({
      name: "keyword-blocklist",
      enabled: false,
    });
  });

  it("saving the check timeout sends only the timeout", async () => {
    renderRules();
    // The base lists no stored MCP Security check (no account field:
    // isShownCheck), so wait for a listed rule.
    await row("keyword-blocklist");
    const pipeline = screen.getByRole("region", { name: "Pipeline" });
    fireEvent.change(within(pipeline).getByLabelText("Check timeout (ms)"), {
      target: { value: "3000" },
    });
    fireEvent.click(
      within(pipeline).getByRole("button", { name: "Save timeout" }),
    );
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toEqual({ guardrails: { timeout_ms: 3000 } });
  });

  it("editing another rule sends that rule alone", async () => {
    const user = userEvent.setup();
    renderRules();
    const drawer = await openRule(
      user,
      "keyword-blocklist",
      "Keyword Blocklist",
    );
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const body = post.mock.calls[0][1];
    expect(body.name).toBe("keyword-blocklist");
    expect(JSON.stringify(body)).not.toMatch(/mcp_security|tool_permission/);
  });
});
