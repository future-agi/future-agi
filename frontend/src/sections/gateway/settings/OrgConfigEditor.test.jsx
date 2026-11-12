import React from "react";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import OrgConfigEditor from "./OrgConfigEditor";
import OrgConfigSection from "./OrgConfigSection";
import { GUARDRAILS_OFF_BANNER } from "../utils/guardrailPipeline";
import { GATEWAY_CONFIG_QUERY_KEY } from "../providers/hooks/useGatewayConfig";

// Saving is Owner or Admin only (useGatewayAdmin); these cases act as an
// Admin. RelfupAdminGatingOwned.test.jsx covers the other levels.
let mockOrganizationId = "org-1";
let mockGatewayId = "gw-1";
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: mockOrgLevel,
    currentOrganizationId: mockOrganizationId,
  }),
}));

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      orgConfig: {
        create: "/agentcc/org-configs/",
        active: "/agentcc/org-configs/active/",
        list: "/agentcc/org-configs/",
        activate: (id) => `/agentcc/org-configs/${id}/activate/`,
        diff: (id) => `/agentcc/org-configs/${id}/diff/`,
      },
      config: (id) => `/agentcc/gateways/${id}/config/`,
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
      reload: (id) => `/agentcc/gateways/${id}/reload/`,
      providers: (id) => `/agentcc/gateways/${id}/providers/`,
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// Use the same grid boundary as OrgConfigSection.test.jsx; the editor,
// history drawer and their query/mutation hooks stay real in the rollback test.
vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  return {
    AgGridReact: forwardRef(function MockGrid() {
      return <div data-testid="ag-grid" />;
    }),
  };
});

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: mockGatewayId }),
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
  <>
    <input
      aria-label="Global RPM"
      type="number"
      value={rateLimiting.global_rpm ?? ""}
      onChange={(event) =>
        onChange({ ...rateLimiting, global_rpm: Number(event.target.value) })
      }
    />
    <button
      type="button"
      onClick={() => onChange({ ...rateLimiting, global_rpm: 120 })}
    >
      Raise global RPM
    </button>
  </>
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

const STORED_CONFIG = {
  ...ACTIVE_CONFIG,
  id: "cfg-6",
  version: 6,
  rate_limiting: { enabled: true, global_rpm: 120 },
};

let client;
function withQueryClient(ui) {
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

beforeEach(() => {
  mockOrganizationId = "org-1";
  mockGatewayId = "gw-1";
  mockOrgLevel = 8;
  enqueueSnackbar.mockClear();
  get.mockReset();
  get.mockImplementation((url) =>
    Promise.resolve({
      data: {
        result: url.endsWith("/org-configs/active/") ? STORED_CONFIG : {},
      },
    }),
  );
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

describe("OrgConfigEditor", () => {
  it.each([false, true, undefined])(
    "shows the Guardrails off banner only when reported off (%s)",
    (enabled) => {
      client.setQueryData([GATEWAY_CONFIG_QUERY_KEY, "gw-1"], {
        guardrail_pipeline: enabled === undefined ? undefined : { enabled },
      });
      render(
        withQueryClient(
          <OrgConfigEditor
            open
            onClose={vi.fn()}
            initialConfig={ACTIVE_CONFIG}
            defaultTab="guardrails"
          />,
        ),
      );
      if (enabled === false)
        expect(screen.getByText(GUARDRAILS_OFF_BANNER)).toBeInTheDocument();
      else expect(screen.queryByText(GUARDRAILS_OFF_BANNER)).toBeNull();
    },
  );

  it("does not report guardrails off while the gateway config is loading", () => {
    get.mockReturnValue(new Promise(() => {}));
    render(
      withQueryClient(
        <OrgConfigEditor
          open
          onClose={vi.fn()}
          initialConfig={ACTIVE_CONFIG}
          defaultTab="guardrails"
        />,
      ),
    );
    expect(
      client.getQueryState([GATEWAY_CONFIG_QUERY_KEY, "gw-1"]).fetchStatus,
    ).toBe("fetching");
    expect(screen.queryByText(GUARDRAILS_OFF_BANNER)).toBeNull();
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

    await user.click(
      screen.getByRole("button", { name: "Raise global RPM" }),
    );
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      "/agentcc/gateways/gw-1/update-config/",
      { rate_limiting: { global_rpm: 120 } },
      expect.any(Object),
    );
    post.mock.calls.forEach(([url]) =>
      expect(url).not.toMatch(/org-configs/),
    );
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
            // The shape this editor saved through POST org-configs/.
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

    await user.click(
      screen.getByRole("button", { name: "Raise global RPM" }),
    );
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

    await user.click(
      screen.getByRole("button", { name: "Raise global RPM" }),
    );
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

describe("stored and applied config receipts", () => {
  function editor(props = {}) {
    return withQueryClient(
      <OrgConfigEditor
        open
        onClose={vi.fn()}
        initialConfig={ACTIVE_CONFIG}
        defaultTab="rate_limiting"
        {...props}
      />,
    );
  }

  async function saveEdit() {
    await userEvent.click(
      screen.getByRole("button", { name: "Raise global RPM" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save & Activate" }),
    );
  }

  it("retains Not applied after toast expiry and closing/reopening, and rebases the stored draft", async () => {
    post.mockResolvedValue({
      data: { result: { version: 6, gateway_synced: false } },
    });
    const onClose = vi.fn();
    const onSaved = vi.fn();
    const { rerender } = render(editor({ onClose, onSaved }));
    await saveEdit();
    expect(
      await screen.findByText(/Saved · Not applied/),
    ).toBeInTheDocument();
    expect(screen.getByText("All edits stored.")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalledWith({
      organizationId: "org-1",
      gatewayId: "gw-1",
      version: 6,
      applyState: "not_applied",
    });
    vi.useFakeTimers();
    try {
      await act(async () => {
        vi.advanceTimersByTime(60000);
      });
      expect(screen.getByText(/Saved · Not applied/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    rerender(editor({ open: false, onClose, onSaved }));
    rerender(editor({ onClose, onSaved }));
    expect(screen.getByText(/Saved · Not applied/)).toBeInTheDocument();
    expect(
      screen.getByRole("spinbutton", { name: "Global RPM" }),
    ).toHaveValue(120);
    expect(screen.getByRole("button", { name: "Retry apply" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Save & Activate" }),
    ).toBeDisabled();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("treats missing apply evidence as unconfirmed, never Applied", async () => {
    post.mockResolvedValue({ data: { result: { version: 6 } } });
    const onClose = vi.fn();
    render(editor({ onClose }));
    await saveEdit();
    expect(
      await screen.findByText("Saved, application not confirmed"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Saved · Applied to gateway"),
    ).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("retries apply once through reload, without another save, and clears the warning only on confirmation", async () => {
    post.mockResolvedValueOnce({
      data: { result: { version: 6, gateway_synced: false } },
    });
    const onSaved = vi.fn();
    render(editor({ onSaved }));
    await saveEdit();
    await screen.findByText(/Saved · Not applied/);
    post.mockResolvedValueOnce({
      data: { result: { status: "ok", gateway_synced: true } },
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Retry apply" }),
    );
    expect(
      await screen.findByText("Saved · Applied to gateway"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Saved · Not applied/)).not.toBeInTheDocument();
    expect(
      post.mock.calls.filter(([url]) => url.endsWith("/update-config/")),
    ).toHaveLength(1);
    expect(
      post.mock.calls.filter(([url]) => url.endsWith("/reload/")),
    ).toHaveLength(1);
    expect(post).toHaveBeenLastCalledWith(
      "/agentcc/gateways/gw-1/reload/",
      {},
      expect.any(Object),
    );
    expect(onSaved).toHaveBeenLastCalledWith(
      expect.objectContaining({ version: 6, applyState: "applied" }),
    );
  });

  it.each(["failed", "not-applied", "unconfirmed"])(
    "keeps the warning after a %s apply retry",
    async (outcome) => {
      post.mockResolvedValueOnce({
        data: { result: { version: 6, gateway_synced: false } },
      });
      render(editor());
      await saveEdit();
      await screen.findByText(/Saved · Not applied/);
      if (outcome === "failed")
        post.mockRejectedValueOnce({ statusCode: 500 });
      else
        post.mockResolvedValueOnce({
          data: {
            result:
              outcome === "not-applied"
                ? { gateway_synced: false }
                : { status: "ok" },
          },
        });
      await userEvent.click(
        screen.getByRole("button", { name: "Retry apply" }),
      );
      expect(
        await screen.findByText(/Retry did not confirm application/),
      ).toBeInTheDocument();
      expect(screen.getByText(/Saved · Not applied/)).toBeInTheDocument();
      expect(
        screen.queryByText("Saved · Applied to gateway"),
      ).not.toBeInTheDocument();
      expect(
        post.mock.calls.filter(([url]) => url.endsWith("/update-config/")),
      ).toHaveLength(1);
    },
  );

  it("keeps edits made during a save and disables retry apply for the newer unsaved draft", async () => {
    let finishSave;
    post.mockReturnValueOnce(
      new Promise((resolve) => {
        finishSave = resolve;
      }),
    );
    const onClose = vi.fn();
    render(editor({ onClose }));
    await saveEdit();
    const rpm = screen.getByRole("spinbutton", { name: "Global RPM" });
    await userEvent.clear(rpm);
    await userEvent.type(rpm, "180");
    await act(async () => {
      finishSave({ data: { result: { version: 6, gateway_synced: false } } });
    });
    await screen.findByText(/Saved · Not applied/);
    expect(rpm).toHaveValue(180);
    expect(
      screen.getByText(/Unsaved changes are not applied/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Retry apply" }),
    ).toBeDisabled();
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.clear(rpm);
    await userEvent.type(rpm, "120");
    expect(screen.getByText("All edits stored.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry apply" })).toBeEnabled();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("scopes an apply receipt to the stored snapshot even if more edits arrive during reload", async () => {
    post.mockResolvedValueOnce({
      data: { result: { version: 6, gateway_synced: false } },
    });
    render(editor());
    await saveEdit();
    await screen.findByText(/Saved · Not applied/);
    let finishReload;
    post.mockReturnValueOnce(
      new Promise((resolve) => {
        finishReload = resolve;
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Retry apply" }),
    );
    const rpm = screen.getByRole("spinbutton", { name: "Global RPM" });
    await userEvent.clear(rpm);
    await userEvent.type(rpm, "180");
    await act(async () => {
      finishReload({ data: { result: { gateway_synced: true } } });
    });
    await screen.findByText("Saved · Applied to gateway");
    expect(
      screen.getByText(/Unsaved changes are not applied/),
    ).toBeInTheDocument();
    expect(rpm).toHaveValue(180);
    expect(
      post.mock.calls.filter(([url]) => url.endsWith("/update-config/")),
    ).toHaveLength(1);
  });

  it.each(["organization", "gateway"])(
    "clears the receipt when the %s changes and ignores late save results",
    async (scope) => {
      post.mockResolvedValueOnce({
        data: { result: { version: 6, gateway_synced: false } },
      });
      const onSaved = vi.fn();
      const { rerender } = render(editor({ onSaved }));
      await saveEdit();
      await screen.findByText(/Saved · Not applied/);
      if (scope === "organization") mockOrganizationId = "org-2";
      else mockGatewayId = "gw-2";
      rerender(editor({ onSaved }));
      expect(
        screen.queryByText(/Saved · Not applied/),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("spinbutton", { name: "Global RPM" }),
      ).toHaveValue(60);
      let finishSave;
      post.mockReturnValueOnce(
        new Promise((resolve) => {
          finishSave = resolve;
        }),
      );
      await saveEdit();
      if (scope === "organization") mockOrganizationId = "org-3";
      else mockGatewayId = "gw-3";
      rerender(editor({ onSaved }));
      await act(async () => {
        finishSave({
          data: { result: { version: 7, gateway_synced: false } },
        });
      });
      expect(
        screen.queryByText(/Saved · Not applied/),
      ).not.toBeInTheDocument();
      expect(onSaved).toHaveBeenCalledTimes(1);
    },
  );

  it("accepts explicit apply confirmation from older responses without a version", async () => {
    post.mockResolvedValueOnce({
      data: { result: { gateway_synced: true } },
    });
    const onClose = vi.fn();
    render(editor({ onClose }));
    await saveEdit();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(
      screen.getByText("Saved · Applied to gateway"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Last save: version not reported."),
    ).toBeInTheDocument();
  });

  it("does not confirm storage without a version or explicit sync outcome", async () => {
    post.mockResolvedValueOnce({ data: { result: {} } });
    const onSaved = vi.fn();
    const onClose = vi.fn();
    render(editor({ onSaved, onClose }));
    await saveEdit();
    expect(
      await screen.findByText(/Storage was not confirmed/),
    ).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByRole("spinbutton", { name: "Global RPM" }),
    ).toHaveValue(120);
  });

  it("treats a fresh read of the pre-save version as a rollback, not stale data", async () => {
    post.mockResolvedValueOnce({
      data: { result: { version: 6, gateway_synced: false } },
    });
    const onSaved = vi.fn();
    const { rerender } = render(
      editor({ onSaved, initialConfigUpdatedAt: 100 }),
    );
    await saveEdit();
    await screen.findByText(/Saved · Not applied/);
    rerender(editor({ open: false, onSaved, initialConfigUpdatedAt: 100 }));
    // React Query can structurally share the same v5 object. Its successful
    // read timestamp, not numeric version ordering, distinguishes this read.
    rerender(editor({ onSaved, initialConfigUpdatedAt: 200 }));
    expect(
      screen.getByRole("spinbutton", { name: "Global RPM" }),
    ).toHaveValue(60);
    const retry = screen.getByRole("button", { name: "Retry apply" });
    expect(retry).toBeDisabled();
    await userEvent.click(retry);
    expect(post).toHaveBeenCalledTimes(1);
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Saved · Applied to gateway")).toBeNull();
  });

  it.each([
    "successful reads",
    "failed activation read",
    "unchanged pre-save cache",
  ])(
    "does not restore or apply v6 after activating v5 in history (%s)",
    async (readMode) => {
      let activeConfig = ACTIVE_CONFIG;
      let saved = false;
      let activated = false;
      get.mockImplementation((url) => {
        if (url.endsWith("/org-configs/active/")) {
          if (
            (readMode === "unchanged pre-save cache" && saved) ||
            (readMode === "failed activation read" && activated)
          ) {
            return Promise.reject({ statusCode: 500 });
          }
          return Promise.resolve({ data: { result: activeConfig } });
        }
        if (url === "/agentcc/org-configs/") {
          const versions = saved
            ? [STORED_CONFIG, ACTIVE_CONFIG]
            : [ACTIVE_CONFIG];
          return Promise.resolve({
            data: {
              result: versions.map((config) => ({
                ...config,
                is_active: config.version === activeConfig.version,
              })),
            },
          });
        }
        if (url.endsWith("/diff/")) {
          return Promise.resolve({
            data: {
              result: {
                from_version: 6,
                to_version: 5,
                rate_limiting: {
                  added: {},
                  removed: {},
                  changed: { global_rpm: { old: 120, new: 60 } },
                },
              },
            },
          });
        }
        return Promise.resolve({ data: { result: {} } });
      });
      post.mockImplementation((url) => {
        if (url.endsWith("/update-config/")) {
          saved = true;
          activeConfig = STORED_CONFIG;
          return Promise.resolve({
            data: { result: { version: 6, gateway_synced: false } },
          });
        }
        if (url.endsWith("/cfg-5/activate/")) {
          activated = true;
          activeConfig = {
            ...ACTIVE_CONFIG,
            budgets: { policy_revision: 2 },
          };
          return Promise.resolve({
            data: { result: { ...activeConfig, gateway_synced: true } },
          });
        }
        return Promise.resolve({
          data: { result: { status: "ok", gateway_synced: true } },
        });
      });
      render(withQueryClient(<OrgConfigSection />));
      await userEvent.click(
        await screen.findByRole("button", { name: "Edit Config" }),
      );
      await userEvent.click(
        screen.getByRole("tab", { name: "Rate Limiting" }),
      );
      await saveEdit();
      await within(screen.getByRole("dialog")).findByText(
        "Saved · Not applied",
      );
      await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

      await userEvent.click(
        screen.getByRole("button", { name: "View History" }),
      );
      const history = await screen.findByRole("dialog", {
        name: "Config History",
      });
      await within(history).findByText("v6");
      await userEvent.click(
        within(history).getByRole("button", { name: "Activate" }),
      );
      const activate = await screen.findByRole("button", {
        name: "Activate v5",
      });
      await waitFor(() => expect(activate).toBeEnabled());
      await userEvent.click(activate);
      await waitFor(() =>
        expect(
          screen.queryByRole("dialog", { name: /Activate v5/ }),
        ).toBeNull(),
      );
      await userEvent.click(
        within(history).getByRole("button", { name: "Close" }),
      );
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

      await userEvent.click(
        screen.getByRole("button", { name: "Edit Config" }),
      );
      await userEvent.click(
        screen.getByRole("tab", { name: "Rate Limiting" }),
      );
      expect(
        screen.getByRole("spinbutton", { name: "Global RPM" }),
      ).toHaveValue(60);
      const dialog = within(screen.getByRole("dialog"));
      expect(dialog.getByText("Last save: version 6.")).toBeInTheDocument();
      expect(dialog.getByText("Saved · Not applied")).toBeInTheDocument();
      const retry = dialog.getByRole("button", { name: "Retry apply" });
      expect(retry).toBeDisabled();
      await userEvent.click(retry);
      expect(screen.queryByText("Saved · Applied to gateway")).toBeNull();
      expect(post.mock.calls.map(([url]) => url)).toEqual([
        "/agentcc/gateways/gw-1/update-config/",
        "/agentcc/org-configs/cfg-5/activate/",
      ]);
    },
  );

  it.each(["different version", "failed read"])(
    "does not reload a retained receipt when the active check returns a %s",
    async (outcome) => {
      post.mockResolvedValueOnce({
        data: { result: { version: 6, gateway_synced: false } },
      });
      const onSaved = vi.fn();
      render(editor({ onSaved }));
      await saveEdit();
      await screen.findByText(/Saved · Not applied/);
      await waitFor(() =>
        expect(
          client.getQueryState(["agentcc-org-config"])?.fetchStatus,
        ).toBe("idle"),
      );
      get.mockImplementation((url) => {
        if (url.endsWith("/org-configs/active/")) {
          return outcome === "failed read"
            ? Promise.reject({ statusCode: 500 })
            : Promise.resolve({ data: { result: ACTIVE_CONFIG } });
        }
        return Promise.resolve({ data: { result: {} } });
      });
      await userEvent.click(
        screen.getByRole("button", { name: "Retry apply" }),
      );
      await screen.findByText(/Retry did not confirm application/);
      expect(post).toHaveBeenCalledTimes(1);
      expect(onSaved).toHaveBeenCalledTimes(1);
      expect(screen.queryByText("Saved · Applied to gateway")).toBeNull();
    },
  );

  it.each(["different version", "failed read"])(
    "does not mark v6 applied when the post-reload check returns a %s",
    async (outcome) => {
      post.mockResolvedValueOnce({
        data: { result: { version: 6, gateway_synced: false } },
      });
      const onSaved = vi.fn();
      render(editor({ onSaved }));
      await saveEdit();
      await screen.findByText(/Saved · Not applied/);
      let finishReload;
      post.mockReturnValueOnce(
        new Promise((resolve) => {
          finishReload = resolve;
        }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Retry apply" }),
      );
      await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
      get.mockImplementation((url) => {
        if (url.endsWith("/org-configs/active/")) {
          return outcome === "failed read"
            ? Promise.reject({ statusCode: 500 })
            : Promise.resolve({ data: { result: ACTIVE_CONFIG } });
        }
        return Promise.resolve({ data: { result: {} } });
      });
      await act(async () => {
        finishReload({ data: { result: { gateway_synced: true } } });
      });
      await screen.findByText(/Retry did not confirm application/);
      expect(screen.getByText("Saved · Not applied")).toBeInTheDocument();
      expect(onSaved).toHaveBeenCalledTimes(1);
      expect(screen.queryByText("Saved · Applied to gateway")).toBeNull();
      expect(post.mock.calls.map(([url]) => url)).toEqual([
        "/agentcc/gateways/gw-1/update-config/",
        "/agentcc/gateways/gw-1/reload/",
      ]);
    },
  );

  it("does not apply an older receipt when a newer stored version arrives", async () => {
    post.mockResolvedValueOnce({
      data: { result: { version: 6, gateway_synced: false } },
    });
    const { rerender } = render(editor());
    await saveEdit();
    await screen.findByText(/Saved · Not applied/);
    rerender(editor({ initialConfig: { ...ACTIVE_CONFIG, version: 7 } }));
    expect(
      screen.getByRole("button", { name: "Retry apply" }),
    ).toBeDisabled();
    expect(
      screen.getByText(/receipt belongs to another saved version/),
    ).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("does not close over later edits even when the earlier save was applied", async () => {
    let finishSave;
    post.mockReturnValueOnce(
      new Promise((resolve) => {
        finishSave = resolve;
      }),
    );
    const onClose = vi.fn();
    render(editor({ onClose }));
    await saveEdit();
    const rpm = screen.getByRole("spinbutton", { name: "Global RPM" });
    await userEvent.clear(rpm);
    await userEvent.type(rpm, "180");
    await act(async () => {
      finishSave({
        data: {
          result: { version: 6, policy_revision: 4, gateway_synced: true },
        },
      });
    });
    await screen.findByText("Saved · Applied to gateway");
    expect(rpm).toHaveValue(180);
    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByText(/Unsaved changes are not applied/),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Save & Activate" }),
    );
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1][1]).toEqual({
      rate_limiting: { global_rpm: 180 },
    });
  });

  it("does not let a read-only member retry apply", async () => {
    post.mockResolvedValueOnce({
      data: { result: { version: 6, gateway_synced: false } },
    });
    const { rerender } = render(editor());
    await saveEdit();
    await screen.findByText(/Saved · Not applied/);
    mockOrgLevel = 3;
    rerender(editor());
    expect(
      screen.getByRole("button", { name: "Retry apply" }),
    ).toBeDisabled();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("keeps unsaved editor fields through provider loading, failure and read retry", async () => {
    let failProviders;
    get.mockImplementation((url) =>
      url.endsWith("/providers/")
        ? new Promise((_, reject) => {
            failProviders = reject;
          })
        : Promise.resolve({ data: { result: {} } }),
    );
    render(editor());
    await userEvent.click(
      screen.getByRole("button", { name: "Raise global RPM" }),
    );
    await userEvent.click(screen.getByRole("tab", { name: "Providers" }));
    expect(screen.getByText(/Loading providers/)).toBeInTheDocument();
    await act(async () => {
      failProviders({ statusCode: 500 });
    });
    await screen.findByText(/Could not load providers/);
    expect(screen.queryByText("No providers yet")).not.toBeInTheDocument();
    get.mockResolvedValue({
      data: {
        result: {
          providers: [{ name: "recovered-provider", models: ["model"] }],
        },
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("recovered-provider");
    expect(
      get.mock.calls.filter(([url]) => url.endsWith("/providers/")),
    ).toHaveLength(2);
    await userEvent.click(screen.getByRole("tab", { name: "Rate Limiting" }));
    expect(
      screen.getByRole("spinbutton", { name: "Global RPM" }),
    ).toHaveValue(120);
    expect(post).not.toHaveBeenCalled();
  });
});

it("shows a server chain-limit 400 and keeps the settings draft open", async () => {
  const message =
    "routing.model_fallbacks.example/primary has 16 fallback models; limit is 15.";
  post.mockRejectedValue({ statusCode: 400, result: message });
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
  await userEvent.click(
    screen.getByRole("button", { name: "Raise global RPM" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Save & Activate" }),
  );
  await waitFor(() =>
    expect(enqueueSnackbar).toHaveBeenCalledWith(message, {
      variant: "error",
    }),
  );
  expect(onClose).not.toHaveBeenCalled();
});
