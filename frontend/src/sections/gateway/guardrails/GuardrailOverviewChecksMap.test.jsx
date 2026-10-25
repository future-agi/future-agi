import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import GuardrailManagementSection from "./GuardrailManagementSection";

// The switch and Edit are Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin. AdminGatingOwned.test.jsx covers the other levels.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

// The Overview's toggle and edit dialog on a section stored as a checks map.
// The gateway runs a map check stored without an action as log, and without
// a threshold at 0, and update-guardrail edits such a section in the map
// (futureagi/agentcc/views/gateway.py; its contract takes only enabled,
// action, confidence_threshold and config on a check). So the dialog opens on
// what the gateway runs, and sends the map's shape: the keys the admin
// changed, by the map's names. A section stored as rules is edited as rules.

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
let mockOrgConfig;

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ tab: undefined }),
    useNavigate: () => vi.fn(),
  };
});

vi.mock("src/utils/axios", () => ({
  default: { post, get: vi.fn() },
  endpoints: {
    gateway: {
      toggleGuardrail: (id) => `/agentcc/gateways/${id}/toggle-guardrail/`,
      updateGuardrail: (id) => `/agentcc/gateways/${id}/update-guardrail/`,
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
      testPlayground: (id) => `/agentcc/gateways/${id}/test-playground/`,
    },
  },
  createQueryString: vi.fn(),
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: mockOrgConfig, isLoading: false }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1", isLoading: false }),
}));

vi.mock("./GuardrailAnalyticsTab", () => ({
  default: () => <div>Analytics tab</div>,
}));

vi.mock("./FeedbackSummaryCard", () => ({
  default: () => <div>Feedback summary</div>,
}));

function renderOverview() {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <GuardrailManagementSection />
    </QueryClientProvider>,
  );
}

const row = (name) => screen.getByText(name).closest("tr");

async function openEdit(name) {
  fireEvent.click(within(row(name)).getByTitle("Edit"));
  return screen.findByRole("dialog");
}

async function pickAction(name) {
  fireEvent.mouseDown(
    within(screen.getByRole("dialog")).getByRole("combobox", {
      name: /Action/,
    }),
  );
  fireEvent.click(await screen.findByRole("option", { name }));
}

const updateGuardrailCalls = () =>
  post.mock.calls.filter(([url]) => url.endsWith("/update-guardrail/"));

describe("Guardrails Overview, a section stored as a checks map", () => {
  beforeEach(() => {
    post.mockReset();
    post.mockResolvedValue({ data: { result: { version: 2 } } });
    mockOrgConfig = {
      guardrails: {
        checks: {
          "data-leakage-prevention": { enabled: true },
          "pii-detection": {
            enabled: true,
            action: "mask",
            confidence_threshold: 0.6,
          },
        },
      },
    };
  });

  it("opens a check stored without an action or a threshold on log and 0", async () => {
    renderOverview();
    const dialog = await openEdit("data-leakage-prevention");

    expect(
      within(dialog).getByRole("combobox", { name: /Action/ }).textContent,
    ).toBe("log");
    expect(within(dialog).getByLabelText("Threshold")).toHaveValue(0);
  });

  it("sends the map's shape: only the key the admin changed", async () => {
    renderOverview();
    await openEdit("data-leakage-prevention");
    await pickAction("block");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateGuardrailCalls()).toHaveLength(1));
    expect(updateGuardrailCalls()[0][1]).toEqual({
      name: "data-leakage-prevention",
      config: { action: "block" },
    });
  });

  it("sends a changed threshold as the map's confidence_threshold", async () => {
    renderOverview();
    const dialog = await openEdit("pii-detection");

    expect(within(dialog).getByLabelText("Threshold")).toHaveValue(0.6);
    fireEvent.change(within(dialog).getByLabelText("Threshold"), {
      target: { value: "0.4" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateGuardrailCalls()).toHaveLength(1));
    expect(updateGuardrailCalls()[0][1]).toEqual({
      name: "pii-detection",
      config: { confidence_threshold: 0.4 },
    });
  });

  it("sends nothing when the admin changed nothing", async () => {
    renderOverview();
    await openEdit("pii-detection");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(updateGuardrailCalls()).toHaveLength(0);
  });

  it("toggles a check by its name in the map", async () => {
    renderOverview();
    fireEvent.click(
      within(row("data-leakage-prevention")).getByRole("checkbox"),
    );

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gateway-1/toggle-guardrail/",
    );
    expect(post.mock.calls[0][1]).toEqual({
      name: "data-leakage-prevention",
      enabled: false,
    });
  });

  it("control: a section stored as rules is edited as a rule", async () => {
    mockOrgConfig = {
      guardrails: {
        rules: [{ name: "pii-detector", enabled: true, action: "mask" }],
      },
    };
    renderOverview();
    await openEdit("pii-detector");
    await pickAction("block");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateGuardrailCalls()).toHaveLength(1));
    expect(updateGuardrailCalls()[0][1]).toEqual({
      name: "pii-detector",
      config: {
        name: "pii-detector",
        enabled: true,
        action: "block",
        mode: "sync",
      },
    });
  });
});
