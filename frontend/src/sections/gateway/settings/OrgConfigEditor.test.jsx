import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import OrgConfigEditor from "./OrgConfigEditor";

// Saving is Owner or Admin only (useGatewayAdmin); these cases act as an
// Admin. AdminGatingOwned.test.jsx covers the other levels.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const { post } = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post, get: vi.fn() },
  endpoints: {
    gateway: {
      orgConfig: { create: "/agentcc/org-configs/" },
      config: (id) => `/agentcc/gateways/${id}/config/`,
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));

vi.mock("./ProviderConfigTab", () => ({
  default: () => <div>Providers tab</div>,
}));

// Stands in for the guardrails editor: one button disables a check the way
// the real tab does. Over stored rules that is the checks map with each
// rule's original name kept; over a stored checks map, which the real tab
// edits as it is, that map with the one check disabled.
const MockGuardrailConfigTab = ({ guardrails, onChange }) => {
  const stored = guardrails?.checks?.["openai-moderation"];
  return (
    <button
      type="button"
      onClick={() =>
        onChange(
          stored
            ? {
                ...guardrails,
                checks: {
                  ...guardrails.checks,
                  "openai-moderation": { ...stored, enabled: false },
                },
              }
            : {
                checks: {
                  "openai-moderation": {
                    enabled: false,
                    action: "block",
                    confidence_threshold: 0.8,
                    config: { api_key: "__encrypted__" },
                    _originalName: "openai-moderation",
                  },
                },
              },
        )
      }
    >
      Disable moderation
    </button>
  );
};
MockGuardrailConfigTab.propTypes = {
  guardrails: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};

vi.mock("./GuardrailConfigTab", () => ({
  default: (props) => <MockGuardrailConfigTab {...props} />,
}));

vi.mock("./RoutingConfigTab", () => ({
  default: () => <div>Routing tab</div>,
}));

vi.mock("./CacheConfigTab", () => ({
  default: () => <div>Cache tab</div>,
}));

// Stands in for the rate-limit form: one button raises the global RPM.
const MockRateLimitingTab = ({ rateLimiting, onChange }) => (
  <button
    type="button"
    onClick={() => onChange({ ...rateLimiting, global_rpm: 120 })}
  >
    Raise global RPM
  </button>
);
MockRateLimitingTab.propTypes = {
  rateLimiting: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};

vi.mock("./RateLimitingConfigTab", () => ({
  default: (props) => <MockRateLimitingTab {...props} />,
}));

vi.mock("./BudgetsConfigTab", () => ({
  default: () => <div>Budgets tab</div>,
}));

vi.mock("./SecurityConfigTab", () => ({
  default: () => <div>Security tab</div>,
}));

vi.mock("./ModelDatabaseConfigTab", () => ({
  default: () => <div>Model database tab</div>,
}));

vi.mock("./ModelMapConfigTab", () => ({
  default: () => <div>Model map tab</div>,
}));

const ACTIVE_CONFIG = {
  id: "cfg-5",
  version: 5,
  providers: { openai: { models: ["gpt-4o"] } },
  routing: { strategy: "cost", auto: { preference: "cost" } },
  rate_limiting: { enabled: true, global_rpm: 60 },
  alerting: {},
  managed_models: { enabled: true },
};

let client;
function withQueryClient(ui) {
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("OrgConfigEditor", () => {
  beforeEach(() => {
    post.mockReset();
    post.mockResolvedValue({
      data: { result: { version: 6, gateway_synced: true } },
    });
    client = new QueryClient({
      mutationCache: new MutationCache({ onError: () => {} }),
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    });
  });

  it("saves a rate-limit edit as a rate_limiting-only patch through update-config", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    render(
      withQueryClient(
        <OrgConfigEditor
          open
          onClose={onClose}
          initialConfig={ACTIVE_CONFIG}
          defaultTab={4}
        />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "Raise global RPM" }));
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      "/agentcc/gateways/gw-1/update-config/",
      { rate_limiting: { global_rpm: 120 } },
      expect.any(Object),
    );
    post.mock.calls.forEach(([url]) => expect(url).not.toMatch(/org-configs/));
  });

  it("saves a guardrails edit as rules, keeping each rule's credential policy", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    render(
      withQueryClient(
        <OrgConfigEditor
          open
          onClose={onClose}
          initialConfig={{
            ...ACTIVE_CONFIG,
            guardrails: {
              rules: [
                {
                  name: "openai-moderation",
                  enabled: true,
                  config: { api_key: "__encrypted__" },
                  _policy: "policy-1",
                },
              ],
            },
          }}
          defaultTab={1}
        />,
      ),
    );

    await user.click(
      screen.getByRole("button", { name: "Disable moderation" }),
    );
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(post).toHaveBeenCalledTimes(1);
    const [, body] = post.mock.calls[0];
    expect(Object.keys(body)).toEqual(["guardrails"]);
    expect(body.guardrails.rules).toEqual([
      expect.objectContaining({
        name: "openai-moderation",
        enabled: false,
        _policy: "policy-1",
      }),
    ]);
  });

  it("saves a guardrails edit over a stored checks map as a patch of that map", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    render(
      withQueryClient(
        <OrgConfigEditor
          open
          onClose={onClose}
          initialConfig={{
            ...ACTIVE_CONFIG,
            // A guardrails section stored as a checks map.
            guardrails: {
              checks: {
                "openai-moderation": {
                  enabled: true,
                  action: "block",
                  confidence_threshold: 0.8,
                  config: { api_key: "__encrypted__" },
                  _policy: "policy-1",
                },
              },
            },
          }}
          defaultTab={1}
        />,
      ),
    );

    await user.click(
      screen.getByRole("button", { name: "Disable moderation" }),
    );
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const [, body] = post.mock.calls[0];
    // The map stays a map, which the push forwards to the gateway as stored:
    // update-config gets the one field changed, with no null under the
    // section, and the check keeps its policy and everything else as stored.
    expect(body).toEqual({
      guardrails: { checks: { "openai-moderation": { enabled: false } } },
    });
  });

  // The gateway connects no organization's MCP server and enforces no org MCP
  // guardrail, so the editor has no MCP tab, and a save leaves a stored mcp
  // section as it is.
  it("offers no MCP tab", () => {
    render(
      withQueryClient(
        <OrgConfigEditor
          open
          onClose={vi.fn()}
          initialConfig={ACTIVE_CONFIG}
        />,
      ),
    );

    expect(screen.getByRole("tab", { name: "Alerting" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "MCP" })).toBeNull();
  });

  it("saves another tab without an mcp section when one is stored", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    render(
      withQueryClient(
        <OrgConfigEditor
          open
          onClose={onClose}
          initialConfig={{
            ...ACTIVE_CONFIG,
            mcp: {
              enabled: true,
              servers: { search: { url: "https://search.example" } },
            },
          }}
          defaultTab={4}
        />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "Raise global RPM" }));
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(post).toHaveBeenCalledTimes(1);
    const [, body] = post.mock.calls[0];
    expect(Object.keys(body)).toEqual(["rate_limiting"]);
  });

  it.each([
    ["Model Database", "Model database tab"],
    ["Model Map", "Model map tab"],
  ])("opens the %s tab by its name", async (name, body) => {
    const user = userEvent.setup();

    render(
      withQueryClient(
        <OrgConfigEditor
          open
          onClose={vi.fn()}
          initialConfig={ACTIVE_CONFIG}
        />,
      ),
    );

    await user.click(screen.getByRole("tab", { name }));

    expect(screen.getByText(body)).toBeInTheDocument();
  });

  it("closes without posting when nothing was edited", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    render(
      withQueryClient(
        <OrgConfigEditor
          open
          onClose={onClose}
          initialConfig={ACTIVE_CONFIG}
          defaultTab={4}
        />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "Save & Activate" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(post).not.toHaveBeenCalled();
  });

  it("stays open when the save fails", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    post.mockRejectedValue({ statusCode: 400, result: "Invalid rate limit" });

    render(
      withQueryClient(
        <OrgConfigEditor
          open
          onClose={onClose}
          initialConfig={ACTIVE_CONFIG}
          defaultTab={4}
        />,
      ),
    );

    await user.click(screen.getByRole("button", { name: "Raise global RPM" }));
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save & Activate" }),
      ).toBeEnabled(),
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it("preserves alerting draft state when initialConfig changes after enabling alerting", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    const initialConfig = {
      providers: {},
      alerting: {},
    };

    const { rerender } = render(
      withQueryClient(
        <OrgConfigEditor
          open
          onClose={onClose}
          initialConfig={initialConfig}
          defaultTab={7}
        />,
      ),
    );

    const alertingToggle = screen.getByRole("checkbox", {
      name: /enable per-org alerting/i,
    });

    expect(alertingToggle).not.toBeChecked();

    await user.click(alertingToggle);

    expect(alertingToggle).toBeChecked();
    expect(screen.getByText("Notification Channels")).toBeInTheDocument();

    rerender(
      withQueryClient(
        <OrgConfigEditor
          open
          onClose={onClose}
          initialConfig={{
            ...initialConfig,
            _refetchVersion: 1,
          }}
          defaultTab={7}
        />,
      ),
    );

    expect(
      screen.getByRole("checkbox", { name: /enable per-org alerting/i }),
    ).toBeChecked();
    expect(screen.getByText("Notification Channels")).toBeInTheDocument();
  });
});
