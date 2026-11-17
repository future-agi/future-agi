import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import RequestLogRetentionCard from "./RequestLogRetentionCard";

const mockGet = vi.fn();
const mockPut = vi.fn();
let mockRole = "Admin";

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    put: (...args) => mockPut(...args),
  },
  endpoints: {
    gateway: { requestLogRetention: "/agentcc/request-log-retention/" },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: mockRole }),
}));

const response = (overrides = {}) => ({
  status: true,
  result: {
    retention_days: null,
    min_retention_days: null,
    keep_forever_required: false,
    effective_cutoff: null,
    next_run_at: "2026-11-15T04:00:00Z",
    estimated_rows: 0,
    estimate_capped: false,
    // The API always sends last_run; a retention that never ran has nulls.
    last_run: {
      started_at: null,
      finished_at: null,
      deleted_rows: 0,
      error: "",
    },
    ...overrides,
  },
});

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RequestLogRetentionCard />
    </QueryClientProvider>,
  );
}

describe("RequestLogRetentionCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRole = "Admin";
    mockGet.mockResolvedValue({ data: response() });
    mockPut.mockResolvedValue({ data: response() });
  });

  it("renders keep forever by default and shows no previous run", async () => {
    renderCard();
    expect(
      await screen.findByRole("radio", { name: /keep forever/i }),
    ).toBeChecked();
    expect(screen.getByText(/not run yet/i)).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith("/agentcc/request-log-retention/");
  });

  it("saves 30 days for an admin", async () => {
    const user = userEvent.setup();
    renderCard();
    await user.click(
      await screen.findByRole("radio", { name: /delete after/i }),
    );
    await user.clear(screen.getByRole("spinbutton", { name: /days/i }));
    await user.type(screen.getByRole("spinbutton", { name: /days/i }), "30");
    await user.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith("/agentcc/request-log-retention/", {
        retention_days: 30,
      }),
    );
  });

  it("saves keep forever as null", async () => {
    mockGet.mockResolvedValue({ data: response({ retention_days: 30 }) });
    const user = userEvent.setup();
    renderCard();
    await user.click(
      await screen.findByRole("radio", { name: /keep forever/i }),
    );
    await user.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith("/agentcc/request-log-retention/", {
        retention_days: null,
      }),
    );
  });

  it("shows minimum days required by budgets and rejects too few days", async () => {
    mockGet.mockResolvedValue({ data: response({ min_retention_days: 31 }) });
    const user = userEvent.setup();
    renderCard();
    expect(
      await screen.findByText(/at least 31 days: a budget needs them/i),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /delete after/i }));
    await user.clear(screen.getByRole("spinbutton", { name: /days/i }));
    await user.type(screen.getByRole("spinbutton", { name: /days/i }), "30");
    expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
  });

  it("disables day-based retention when a total budget requires forever", async () => {
    mockGet.mockResolvedValue({
      data: response({ keep_forever_required: true }),
    });
    renderCard();
    expect(
      await screen.findByRole("radio", { name: /delete after/i }),
    ).toBeDisabled();
    expect(
      screen.getByText(
        /a total budget is configured, so request logs are kept forever/i,
      ),
    ).toBeInTheDocument();
  });

  it("shows capped estimates and the previous cleanup", async () => {
    mockGet.mockResolvedValue({
      data: response({
        retention_days: 30,
        estimated_rows: 10000,
        estimate_capped: true,
        last_run: {
          started_at: "2026-11-14T04:00:00Z",
          finished_at: "2026-11-14T04:01:00Z",
          deleted_rows: 27,
          error: "",
        },
      }),
    });
    renderCard();
    expect(await screen.findByText(/about 10,000\+ rows/i)).toBeInTheDocument();
    expect(screen.getByText(/27 rows deleted/i)).toBeInTheDocument();
    expect(screen.getByText(/next cleanup/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/In progress|Previous run:/),
    ).not.toBeInTheDocument();
  });

  it("previews the row estimate for unsaved valid days", async () => {
    mockGet.mockImplementation((_, options) =>
      Promise.resolve({
        data: response({
          retention_days: null,
          estimated_rows: options?.params?.retention_days === 30 ? 42 : 0,
        }),
      }),
    );
    const user = userEvent.setup();
    renderCard();
    await user.click(
      await screen.findByRole("radio", { name: /delete after/i }),
    );
    expect(await screen.findByText(/about 42 rows/i)).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith("/agentcc/request-log-retention/", {
      params: { retention_days: 30 },
    });
  });

  it("disables Save for non-admins", async () => {
    mockRole = "Member";
    renderCard();
    expect(await screen.findByRole("button", { name: /save/i })).toBeDisabled();
  });

  it("shows the server's 400 message inline", async () => {
    mockGet.mockResolvedValue({ data: response({ retention_days: 30 }) });
    mockPut.mockRejectedValue({
      response: {
        status: 400,
        data: {
          status: false,
          result:
            "Retention must be at least 31 days because a monthly budget is configured",
        },
      },
    });
    const user = userEvent.setup();
    renderCard();
    await user.click(
      await screen.findByRole("radio", { name: /keep forever/i }),
    );
    await user.click(screen.getByRole("button", { name: /save/i }));
    expect(
      await screen.findByText(
        /retention must be at least 31 days because a monthly budget is configured/i,
      ),
    ).toBeInTheDocument();
  });

  it("shows a cleanup that started but has not finished as in progress", async () => {
    mockGet.mockResolvedValue({
      data: response({
        last_run: {
          started_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
          finished_at: null,
          deleted_rows: 0,
          error: "",
        },
      }),
    });
    renderCard();
    expect(
      await screen.findByText(/In progress · started/),
    ).toBeInTheDocument();
  });

  it("shows a newer running cleanup above the previous finished run", async () => {
    mockGet.mockResolvedValue({
      data: response({
        last_run: {
          started_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
          finished_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
          deleted_rows: 27,
          error: "",
        },
      }),
    });
    renderCard();
    expect(
      await screen.findByText(/In progress · started/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Previous run: .* · 27 rows deleted/),
    ).toBeInTheDocument();
  });

  it("marks an unfinished cleanup from two hours ago as cut short", async () => {
    mockGet.mockResolvedValue({
      data: response({
        last_run: {
          started_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
          finished_at: null,
          deleted_rows: 0,
          error: "",
        },
      }),
    });
    renderCard();
    expect(
      await screen.findByText(/Did not finish · started/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/In progress/)).not.toBeInTheDocument();
  });

  it("shows the previous run when the new cleanup did not finish", async () => {
    mockGet.mockResolvedValue({
      data: response({
        last_run: {
          started_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
          finished_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
          deleted_rows: 42,
          error: "",
        },
      }),
    });
    renderCard();
    expect(
      await screen.findByText(/Did not finish · started/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Previous run: .* · 42 rows deleted/),
    ).toBeInTheDocument();
  });

  it("labels the error of the previous run while a new one runs", async () => {
    mockGet.mockResolvedValue({
      data: response({
        last_run: {
          started_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
          finished_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
          deleted_rows: 3,
          error: "canceling statement due to lock timeout",
        },
      }),
    });
    renderCard();
    expect(
      await screen.findByText(
        "Previous run: canceling statement due to lock timeout",
      ),
    ).toBeInTheDocument();
  });

  it("explains which budget periods cleanup keeps", async () => {
    renderCard();
    expect(
      await screen.findByText(
        "Each run keeps the current period of every budget, and by default the current calendar month and week (UTC) for the gateway's own budgets.",
      ),
    ).toBeInTheDocument();
  });
});
