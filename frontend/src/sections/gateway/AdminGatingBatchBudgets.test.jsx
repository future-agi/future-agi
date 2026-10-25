import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  within,
  waitFor,
} from "src/utils/test-utils";
import GatewaySettingsSection from "./settings/GatewaySettingsSection";
import BudgetEnforcementCard from "./budgets/BudgetEnforcementCard";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "./testing/adminOnly";

// The gateway admin role check on Settings > Batch Jobs and on the Budgets
// page's "Enforce budgets" switch: submitting or cancelling a batch
// (submit_batch, cancel_batch) and the switch (update_config) are Owner or
// Admin only, so for a Member, a Viewer or a user whose level is not loaded
// each control is disabled with "Owner or Admin only". Reading stays open:
// the Batch API card, a batch's details and the budgets caption.

let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => ({ tab: "batch-jobs" }),
  };
});

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: {
    gateway: { healthCheck: (id) => `/agentcc/gateways/${id}/health_check/` },
  },
}));

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual("@tanstack/react-query");
  return {
    ...actual,
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
    useMutation: () => ({ isPending: false, mutate: vi.fn() }),
  };
});

const mockUpdateConfig = vi.fn();
vi.mock("./providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: {
      batch: {
        storage: "memory",
        default_max_concurrency: 10,
        max_body_bytes: 10485760,
      },
    },
    isLoading: false,
    refetch: vi.fn(),
  }),
  useReloadConfig: () => ({ isPending: false, mutate: vi.fn() }),
  useUpdateConfig: () => ({ isPending: false, mutate: mockUpdateConfig }),
}));

vi.mock("./context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gateway-1",
    isLoading: false,
    gateway: { id: "gateway-1", name: "Gateway", status: "healthy" },
  }),
}));

vi.mock("./settings/OrgConfigSection", () => ({ default: () => null }));
vi.mock("./settings/EmailAlertsCard", () => ({ default: () => null }));

const BATCH_ID = "batch-adm-0123456789abcdef";
const mockSubmit = vi.fn((_vars, { onSuccess }) =>
  onSuccess({ batch_id: BATCH_ID }),
);
const mockCancel = vi.fn();
vi.mock("./settings/hooks/useBatchJobs", () => ({
  useSubmitBatch: () => ({ mutate: mockSubmit, isPending: false }),
  useBatchStatus: () => ({
    data: { status: "processing", total: 1, summary: { completed: 0 } },
    isLoading: false,
  }),
  useCancelBatch: () => ({ mutate: mockCancel, isPending: false }),
}));

const submitButton = () =>
  screen.getByRole("button", { name: "Submit Batch Job" });
// The tracked jobs table holds only each row's View Details and Cancel.
const rowButton = (name) =>
  within(screen.getByRole("table")).getByRole("button", { name });
const CANCEL = /^Cancel/;

// Submits one batch as an Admin, so its row (with Cancel) is listed, then
// shows the tab again at the level under test.
async function renderWithATrackedBatch(level) {
  mockOrgLevel = 8;
  const view = render(<GatewaySettingsSection />);
  fireEvent.click(submitButton());
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  expect(mockSubmit).toHaveBeenCalledTimes(1);
  // The submit dialog closes (its exit transition hides the page until done).
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  mockOrgLevel = level;
  view.rerender(<GatewaySettingsSection />);
  return view;
}

beforeEach(() => {
  mockOrgLevel = 8;
  mockSubmit.mockClear();
  mockCancel.mockClear();
  mockUpdateConfig.mockClear();
});

describe("Settings > Batch Jobs", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s reads the batch settings with Submit Batch Job disabled",
    (_who, level) => {
      mockOrgLevel = level;
      render(<GatewaySettingsSection />);
      expect(
        screen.getByText(
          "In this gateway node's memory: a restart loses its batches",
        ),
      ).toBeInTheDocument();
      expectAdminOnly(submitButton());
      fireEvent.click(submitButton());
      expect(
        screen.queryByRole("button", { name: "Submit" }),
      ).not.toBeInTheDocument();
    },
  );

  it.each(ADMIN_LEVELS)("%s can submit a batch job", (_who, level) => {
    mockOrgLevel = level;
    render(<GatewaySettingsSection />);
    expectEditable(submitButton());
    fireEvent.click(submitButton());
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(mockSubmit).toHaveBeenCalledTimes(1);
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s can view a batch's details but not cancel it",
    async (_who, level) => {
      await renderWithATrackedBatch(level);
      expectAdminOnly(rowButton(CANCEL));
      expectEditable(rowButton("View Details"));
      fireEvent.click(rowButton(CANCEL));
      expect(mockCancel).not.toHaveBeenCalled();
    },
  );

  it.each(ADMIN_LEVELS)(
    "%s can cancel a running batch",
    async (_who, level) => {
      await renderWithATrackedBatch(level);
      const cancel = rowButton(CANCEL);
      expectEditable(cancel);
      fireEvent.click(cancel);
      expect(mockCancel).toHaveBeenCalledWith({
        gatewayId: "gateway-1",
        batchId: BATCH_ID,
      });
    },
  );
});

describe("Budgets > Enforce budgets", () => {
  const renderCard = () =>
    render(
      <BudgetEnforcementCard
        gatewayId="gateway-1"
        budgets={{ enabled: true }}
        sectionsEnforced={["budgets"]}
      />,
    );
  const enforceSwitch = () =>
    screen.getByRole("checkbox", { name: /Enforce budgets/i });

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads the switch but cannot turn it",
    (_who, level) => {
      mockOrgLevel = level;
      renderCard();
      expect(enforceSwitch()).toBeChecked();
      expectAdminOnly(enforceSwitch());
      expect(
        screen.getByText(
          "Set for this organization here or in Org Config > Budgets.",
        ),
      ).toBeInTheDocument();
      fireEvent.click(enforceSwitch());
      expect(mockUpdateConfig).not.toHaveBeenCalled();
    },
  );

  it.each(ADMIN_LEVELS)("%s can turn budgets off", (_who, level) => {
    mockOrgLevel = level;
    renderCard();
    expectEditable(enforceSwitch());
    fireEvent.click(enforceSwitch());
    expect(mockUpdateConfig).toHaveBeenCalledWith(
      { gatewayId: "gateway-1", config: { budgets: { enabled: false } } },
      expect.any(Object),
    );
  });
});
