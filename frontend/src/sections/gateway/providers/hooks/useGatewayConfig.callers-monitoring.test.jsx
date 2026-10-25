// The alerting callers of useUpdateConfig on a save the
// gateway did not apply (gateway_synced:false). Each shows exactly one
// toast, the warning, never its success toast and never its generic
// "Failed to ..." toast.
//
//   monitoring/CreateAlertRuleDialog.jsx      create alert rule
//   monitoring/CreateChannelDialog.jsx        create channel
//   monitoring/AlertingMonitoringSection.jsx  delete rule, delete channel
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import CreateAlertRuleDialog from "../../monitoring/CreateAlertRuleDialog";
import CreateChannelDialog from "../../monitoring/CreateChannelDialog";
import AlertingMonitoringSection from "../../monitoring/AlertingMonitoringSection";

// Budget, alerting, routing and fallback writes are Owner or Admin only
// (useGatewayAdmin); these cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const WARNING =
  "Config saved but gateway sync failed. Changes will apply on next gateway restart.";
const SENTENCE = `Saved, but the gateway did not apply it: ${WARNING}`;

const { post, get, snackbars, route } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  snackbars: [],
  route: { tab: "rules" },
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
        providers: p("providers"),
        updateConfig: p("update-config"),
      },
    },
  };
});

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ tab: route.tab }),
  useNavigate: () => vi.fn(),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", isLoading: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: null }),
}));

const CONFIG = {
  alerting: {
    enabled: true,
    rules: [
      {
        name: "high-errors",
        metric: "error_count",
        condition: ">=",
        threshold: 10,
      },
    ],
    channels: [
      { name: "ops", type: "webhook", url: "https://hooks.example/ops" },
    ],
  },
};

function renderWithClient(ui) {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

const expectOnlyTheWarning = () =>
  expect(snackbars).toEqual([
    { message: SENTENCE, options: { variant: "warning" } },
  ]);

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  snackbars.length = 0;
  route.tab = "rules";
  get.mockImplementation(async (url) =>
    url.endsWith("/config/")
      ? { data: { status: true, result: CONFIG } }
      : { data: { status: true, result: null } },
  );
  post.mockResolvedValue({
    data: {
      status: true,
      result: { version: 9, gateway_synced: false, gateway_warning: WARNING },
    },
  });
});

describe("alerting callers on gateway_synced:false", () => {
  it("Create alert rule: the warning and the dialog's Alert, no 'created' toast", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithClient(
      <CreateAlertRuleDialog
        open
        onClose={onClose}
        gatewayId="gw-1"
        existingRules={CONFIG.alerting.rules}
      />,
    );
    await user.type(screen.getByLabelText(/Rule Name/), "new-rule");
    await user.type(screen.getByLabelText(/Threshold/), "5");
    await user.click(screen.getByRole("button", { name: "Create Rule" }));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Create channel: the warning and the dialog's Alert, no 'created' toast", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithClient(
      <CreateChannelDialog
        open
        onClose={onClose}
        gatewayId="gw-1"
        existingChannels={CONFIG.alerting.channels}
      />,
    );
    await user.type(screen.getByLabelText(/Channel Name/), "oncall");
    await user.type(
      screen.getByLabelText(/URL/),
      "https://hooks.example/oncall",
    );
    await user.click(screen.getByRole("button", { name: "Add Channel" }));

    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Delete rule: the warning, no 'deleted' and no 'Failed to delete' toast", async () => {
    const user = userEvent.setup();
    renderWithClient(<AlertingMonitoringSection />);
    await user.click(
      await screen.findByRole("button", { name: "Delete rule high-errors" }),
    );
    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
  });

  it("Delete channel: the warning, no 'deleted' and no 'Failed to delete' toast", async () => {
    route.tab = "channels";
    const user = userEvent.setup();
    renderWithClient(<AlertingMonitoringSection />);
    await user.click(
      await screen.findByRole("button", { name: "Delete channel ops" }),
    );
    await waitFor(() => expect(snackbars).toHaveLength(1));
    expectOnlyTheWarning();
  });
});
