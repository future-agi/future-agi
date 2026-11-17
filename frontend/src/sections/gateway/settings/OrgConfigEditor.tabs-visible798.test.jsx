import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "src/utils/test-utils";
import OrgConfigEditor from "./OrgConfigEditor";

const { post, get, org, stub } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  org: { level: 8 },
  stub: (label) => () => ({ default: () => `${label} body` }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));
vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      config: (id) => `/agentcc/gateways/${id}/config/`,
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-one" }),
}));
vi.mock("./ProviderConfigTab", stub("Providers"));
vi.mock("./GuardrailConfigTab", stub("Guardrails"));
vi.mock("./RoutingConfigTab", stub("Routing"));
vi.mock("./RateLimitingConfigTab", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ onChange }) => (
    <button type="button" onClick={() => onChange({ global_rpm: 120 })}>
      Edit rate limit
    </button>
  ),
}));
vi.mock("./BudgetsConfigTab", stub("Budgets"));
vi.mock("./SecurityConfigTab", stub("Security"));
vi.mock("./AlertingConfigTab", stub("Alerting"));
vi.mock("./ModelDatabaseConfigTab", stub("Model database"));
vi.mock("./ModelMapConfigTab", stub("Model map"));

const STORED = {
  cache: { enabled: true, default_ttl: "5m" },
  a2a: {
    enabled: true,
    card: { name: "Support agent" },
    agents: {
      helper: {
        url: "https://agent.example",
        auth: { type: "bearer", token: "synthetic-agent-token" },
      },
    },
  },
  audit: {
    enabled: true,
    min_severity: "warn",
    sinks: [
      {
        type: "webhook",
        url: "https://audit.example/synthetic-webhook-token",
        headers: { "X-Key": "synthetic-header-token" },
      },
    ],
  },
};
const NOTICES = {
  a2a: "A2A settings are read-only here. This editor does not send A2A settings when you save.",
  audit:
    "Audit settings are read-only here. This editor does not send audit settings when you save.",
};

function renderEditor(defaultTab = "providers", initialConfig = STORED) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onClose = vi.fn();
  const rendered = render(
    <QueryClientProvider client={client}>
      <OrgConfigEditor
        open
        onClose={onClose}
        initialConfig={initialConfig}
        defaultTab={defaultTab}
      />
    </QueryClientProvider>,
  );
  return { ...rendered, onClose };
}

beforeEach(() => {
  vi.clearAllMocks();
  org.level = 8;
  get.mockResolvedValue({
    data: { result: { org_sections_enforced: ["cache"] } },
  });
  post.mockResolvedValue({ data: { result: { gateway_synced: true } } });
});

describe("798: visible, read-only organization tabs", () => {
  it("appends both tabs without changing existing tab indices or navigation", () => {
    renderEditor();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Providers",
      "Guardrails",
      "Routing",
      "Cache",
      "Rate Limiting",
      "Budgets",
      "Security",
      "Alerting",
      "Model Database",
      "Model Map",
      "A2A",
      "Audit",
    ]);
    fireEvent.click(screen.getByRole("tab", { name: "Routing" }));
    expect(screen.getByText("Routing body")).toBeInTheDocument();
    for (const [id, name] of [
      ["a2a", "A2A"],
      ["audit", "Audit"],
    ]) {
      fireEvent.click(screen.getByRole("tab", { name }));
      expect(screen.getByText(NOTICES[id])).toBeInTheDocument();
    }
  });

  describe.each(["a2a", "audit"])("%s stored values", (id) => {
    it.each([15, 8, 3, 1])(
      "is read-only even for org level %s",
      async (level) => {
        org.level = level;
        renderEditor(id);
        expect(screen.getByText(NOTICES[id])).toBeInTheDocument();
        expect(
          screen.getByText(NOTICES[id]).closest('[role="alert"]'),
        ).toBeNull();
        const inputs = screen
          .getByRole("dialog")
          .querySelectorAll("input, textarea, select");
        expect(inputs.length).toBeGreaterThan(0);
        for (const input of inputs) {
          expect(input.readOnly || input.disabled).toBe(true);
        }
        const stored = screen.getByRole("textbox", {
          name: /Stored .* settings/,
        });
        expect(stored.value).toContain(id === "a2a" ? "Support agent" : "warn");
        expect(stored.value).not.toMatch(
          /synthetic-(agent|header|webhook)-token/,
        );
        const original = stored.value;
        await userEvent.type(stored, "changed");
        expect(stored).toHaveValue(original);
        expect(post).not.toHaveBeenCalled();
      },
    );

    it("remains visible and read-only when no section is stored", () => {
      renderEditor(id, {});
      expect(screen.getByText(NOTICES[id])).toBeInTheDocument();
      expect(
        screen.queryByRole("textbox", { name: /Stored .* settings/ }),
      ).toBeNull();
      expect(
        screen.getByText(
          `No ${id === "a2a" ? "A2A" : "audit"} settings are stored.`,
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByText(NOTICES[id]).closest('[role="alert"]'),
      ).toBeNull();
      expect(post).not.toHaveBeenCalled();
    });

    it("never sends either section when another tab is edited and saved", async () => {
      const snapshot = JSON.stringify(STORED);
      const { onClose } = renderEditor(id);
      expect(screen.getByText(NOTICES[id])).toBeInTheDocument();
      fireEvent.click(screen.getByRole("tab", { name: "Rate Limiting" }));
      fireEvent.click(screen.getByRole("button", { name: "Edit rate limit" }));
      fireEvent.click(screen.getByRole("button", { name: "Save & Activate" }));
      await waitFor(() => expect(onClose).toHaveBeenCalled());
      expect(post).toHaveBeenCalledTimes(1);
      expect(post.mock.calls[0][0]).toBe(
        "/agentcc/gateways/gw-one/update-config/",
      );
      const payload = post.mock.calls[0][1];
      expect(payload).toEqual({ rate_limiting: { global_rpm: 120 } });
      expect(payload).not.toHaveProperty("cache");
      expect(payload).not.toHaveProperty("a2a");
      expect(payload).not.toHaveProperty("audit");
      expect(JSON.stringify(STORED)).toBe(snapshot);
    });

    it("does not send a request on a read-only, unchanged save", async () => {
      const { onClose } = renderEditor(id);
      expect(screen.getByText(NOTICES[id])).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Save & Activate" }));
      await waitFor(() => expect(onClose).toHaveBeenCalled());
      expect(post).not.toHaveBeenCalled();
    });
  });
});
