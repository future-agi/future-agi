import { describe, it, expect, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    post: (...args) => mockPost(...args),
    put: (...args) => mockPut(...args),
    delete: (...args) => mockDelete(...args),
  },
  endpoints: {
    settings: {
      v2: {
        budgets: "/usage/v2/budgets/",
        budgetDetail: (id) => `/usage/v2/budgets/${id}/`,
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

function renderWithQuery(ui) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("BudgetManager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPost.mockResolvedValue({ data: { result: {} } });
    mockPut.mockResolvedValue({ data: { result: {} } });
    mockDelete.mockResolvedValue({ data: { result: {} } });
  });

  it("renders staged threshold state for existing budgets", async () => {
    mockGet.mockResolvedValue({
      data: {
        result: {
          budgets: [
            {
              id: 7,
              name: "AI Credits cap",
              scope: "ai_credits",
              threshold_value: "5000",
              action: "warn",
              is_active: true,
              notify_emails: ["ops@example.com"],
              notify_slack_webhook:
                "https://hooks.slack.com/services/T000/B000/EXISTING",
              thresholds: [
                { percent: 50, enabled: true, severity: "info" },
                { percent: 80, enabled: false, severity: "warning" },
                { percent: 100, enabled: true, severity: "critical" },
              ],
            },
          ],
        },
      },
    });

    const { default: BudgetManager } = await import("../BudgetManager");
    renderWithQuery(<BudgetManager />);

    expect(await screen.findByText("AI Credits cap")).toBeInTheDocument();
    expect(screen.getByText("50% Early warning")).toBeInTheDocument();
    expect(screen.getByText("80% Off")).toBeInTheDocument();
    expect(screen.getByText("100% Limit reached")).toBeInTheDocument();
    expect(screen.getByText("Slack webhook")).toBeInTheDocument();
  });

  it("submits threshold stages and notification channels when creating a budget", async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue({ data: { result: { budgets: [] } } });

    const { default: BudgetManager } = await import("../BudgetManager");
    renderWithQuery(<BudgetManager />);

    await screen.findByText(/No budgets set/i);

    await user.click(screen.getByRole("button", { name: /add budget/i }));
    await user.type(
      screen.getByLabelText(/budget name/i),
      "AI Credits guardrail",
    );
    await user.type(screen.getByLabelText(/^threshold$/i), "5000");
    await user.type(
      screen.getByLabelText(/notification emails/i),
      "ops@example.com, finance@example.com",
    );
    await user.type(
      screen.getByLabelText(/slack webhook/i),
      "https://hooks.slack.com/services/T000/B000/SECRET",
    );
    await user.click(screen.getByLabelText("Alert at 80%"));
    await user.click(screen.getByRole("button", { name: /create budget/i }));

    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(mockPost).toHaveBeenCalledWith("/usage/v2/budgets/", {
      name: "AI Credits guardrail",
      scope: "ai_credits",
      threshold_value: "5000",
      action: "notify",
      is_active: true,
      notify_emails: ["ops@example.com", "finance@example.com"],
      notify_slack_webhook: "https://hooks.slack.com/services/T000/B000/SECRET",
      thresholds: [
        { percent: 50, enabled: true, severity: "info" },
        { percent: 80, enabled: false, severity: "warning" },
        { percent: 100, enabled: true, severity: "critical" },
      ],
    });
  });

  it("loads and clears an existing Slack webhook when editing", async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue({
      data: {
        result: {
          budgets: [
            {
              id: 9,
              name: "Gateway guardrail",
              scope: "gateway_requests",
              threshold_value: "10000",
              action: "pause",
              is_active: true,
              notify_emails: [],
              notify_slack_webhook:
                "https://hooks.slack.com/services/T000/B000/OLD",
              thresholds: [
                { percent: 50, enabled: true, severity: "info" },
                { percent: 80, enabled: true, severity: "warning" },
                { percent: 100, enabled: true, severity: "critical" },
              ],
            },
          ],
        },
      },
    });

    const { default: BudgetManager } = await import("../BudgetManager");
    renderWithQuery(<BudgetManager />);

    expect(await screen.findByText("Gateway guardrail")).toBeInTheDocument();

    await user.click(screen.getByTitle("Edit budget"));
    const slackInput = screen.getByLabelText(/slack webhook/i);
    expect(slackInput).toHaveValue(
      "https://hooks.slack.com/services/T000/B000/OLD",
    );

    await user.clear(slackInput);
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
    expect(mockPut).toHaveBeenCalledWith("/usage/v2/budgets/9/", {
      name: "Gateway guardrail",
      scope: "gateway_requests",
      threshold_value: "10000",
      action: "pause",
      is_active: true,
      notify_emails: [],
      notify_slack_webhook: null,
      thresholds: [
        { percent: 50, enabled: true, severity: "info" },
        { percent: 80, enabled: true, severity: "warning" },
        { percent: 100, enabled: true, severity: "critical" },
      ],
    });
  });

  it("toggles an existing budget active state without deleting it", async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue({
      data: {
        result: {
          budgets: [
            {
              id: 9,
              name: "Gateway guardrail",
              scope: "gateway_requests",
              threshold_value: "10000",
              action: "pause",
              is_active: false,
              notify_emails: [],
              notify_slack_webhook: null,
              thresholds: [
                { percent: 50, enabled: true, severity: "info" },
                { percent: 80, enabled: true, severity: "warning" },
                { percent: 100, enabled: true, severity: "critical" },
              ],
            },
          ],
        },
      },
    });

    const { default: BudgetManager } = await import("../BudgetManager");
    renderWithQuery(<BudgetManager />);

    expect(await screen.findByText("Gateway guardrail")).toBeInTheDocument();
    expect(screen.getByText("Disabled")).toBeInTheDocument();

    const activeSwitch = screen.getByLabelText("Gateway guardrail active");
    expect(activeSwitch).not.toBeChecked();

    await user.click(activeSwitch);

    await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
    expect(mockPut).toHaveBeenCalledWith("/usage/v2/budgets/9/", {
      is_active: true,
    });
    expect(mockDelete).not.toHaveBeenCalled();
  });

  it("disables an active budget with a partial update", async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue({
      data: {
        result: {
          budgets: [
            {
              id: 10,
              name: "AI spend guardrail",
              scope: "ai_credits",
              threshold_value: "5000",
              action: "warn",
              is_active: true,
              notify_emails: [],
              notify_slack_webhook: null,
              thresholds: [
                { percent: 50, enabled: true, severity: "info" },
                { percent: 80, enabled: true, severity: "warning" },
                { percent: 100, enabled: true, severity: "critical" },
              ],
            },
          ],
        },
      },
    });

    const { default: BudgetManager } = await import("../BudgetManager");
    renderWithQuery(<BudgetManager />);

    expect(await screen.findByText("AI spend guardrail")).toBeInTheDocument();

    await user.click(screen.getByLabelText("AI spend guardrail active"));

    await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
    expect(mockPut).toHaveBeenCalledWith("/usage/v2/budgets/10/", {
      is_active: false,
    });
    expect(mockDelete).not.toHaveBeenCalled();
  });
});
