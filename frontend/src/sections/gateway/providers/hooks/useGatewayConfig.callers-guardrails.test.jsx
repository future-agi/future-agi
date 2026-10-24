// The guardrail callers of useGatewayConfig's writes on
// a save the gateway did not apply (gateway_synced:false). Each shows
// exactly one toast, the warning, never its success toast and never its
// generic "Failed to ..." toast.
//
//   guardrails/GuardrailManagementSection.jsx  Overview switch (useToggleGuardrail)
//   guardrails/EditGuardrailDialog.jsx         Edit guardrail (useUpdateGuardrail)
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import GuardrailManagementSection from "../../guardrails/GuardrailManagementSection";
import EditGuardrailDialog from "../../guardrails/EditGuardrailDialog";

// Gateway config writes are Owner or Admin only (useGatewayAdmin); these
// cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const WARNING =
  "Config saved but gateway sync failed. Changes will apply on next gateway restart.";
const SENTENCE = `Saved, but the gateway did not apply it: ${WARNING}`;

const { post, get, snackbars } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  snackbars: [],
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

vi.mock("src/utils/axios", () => {
  const p = (name) => (id) => `/agentcc/gateways/${id}/${name}/`;
  return {
    default: { post, get },
    endpoints: {
      gateway: {
        config: p("config"),
        toggleGuardrail: p("toggle-guardrail"),
        updateGuardrail: p("update-guardrail"),
        updateConfig: p("update-config"),
        testPlayground: p("test-playground"),
      },
    },
    createQueryString: vi.fn(),
  };
});

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useParams: () => ({}), useNavigate: () => vi.fn() };
});

vi.mock("../../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({
    data: {
      guardrails: {
        rules: [
          {
            name: "pii-detector",
            stage: "pre",
            action: "block",
            enabled: true,
          },
        ],
      },
    },
    isLoading: false,
  }),
}));

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", isLoading: false }),
}));
vi.mock("../../guardrails/GuardrailAnalyticsTab", () => ({
  default: () => null,
}));
vi.mock("../../guardrails/FeedbackSummaryCard", () => ({
  default: () => null,
}));

let client;
function renderWithClient(ui) {
  client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  snackbars.length = 0;
  get.mockResolvedValue({ data: { status: true, result: {} } });
  post.mockResolvedValue({
    data: {
      status: true,
      result: { gateway_synced: false, gateway_warning: WARNING },
    },
  });
});

describe("guardrail callers on gateway_synced:false", () => {
  it("Overview switch: the warning, no 'pii-detector disabled' and no 'Failed to toggle'", async () => {
    const user = userEvent.setup();
    renderWithClient(<GuardrailManagementSection />);
    await user.click(await screen.findByRole("checkbox"));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expect(snackbars).toEqual([
      { message: SENTENCE, options: { variant: "warning" } },
    ]);
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/toggle-guardrail/",
    );
  });

  it("Edit guardrail: the warning and the dialog's Alert, no 'updated' toast, the dialog stays open", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithClient(
      <EditGuardrailDialog
        open
        onClose={onClose}
        guardrail={{ name: "pii-detector", action: "block", enabled: true }}
        gatewayId="gw-1"
      />,
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expect(snackbars).toEqual([
      { message: SENTENCE, options: { variant: "warning" } },
    ]);
    expect(post.mock.calls[0][0]).toBe(
      "/agentcc/gateways/gw-1/update-guardrail/",
    );
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("control: a synced toggle still says 'disabled'", async () => {
    post.mockResolvedValue({
      data: { status: true, result: { gateway_synced: true } },
    });
    const user = userEvent.setup();
    renderWithClient(<GuardrailManagementSection />);
    await user.click(await screen.findByRole("checkbox"));
    await waitFor(() => expect(snackbars).toHaveLength(1));
    expect(snackbars[0]).toEqual({
      message: "pii-detector disabled",
      options: { variant: "success" },
    });
  });
});
