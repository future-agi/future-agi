import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const listHarnessJobs = vi.fn();
const navigate = vi.fn();

vi.mock("src/api/harness/harness", () => ({
  listHarnessJobs: (...args) => listHarnessJobs(...args),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const { default: HarnessList } = await import("./HarnessList");

const job = (id, name, stage) => ({
  job: { job_id: id, run_id: `harness-${id}`, metadata: { agent_name: name } },
  status: { stage, updated_at: "2026-08-25T10:00:00Z" },
  credentials: { detected_connectors: ["http", "livekit"] },
});

// react-query retries by default, which turns a rejected query into a slow test.
const renderList = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <HarnessList />
      </QueryClientProvider>
    </HelmetProvider>,
  );
};

describe("HarnessList", () => {
  beforeEach(() => {
    listHarnessJobs.mockReset();
    navigate.mockReset();
  });

  it("renders a row per environment", async () => {
    listHarnessJobs.mockResolvedValue([
      job("a", "ride-voice-e2e-3", "understanding_agent"),
      job("b", "ride-voice-smoke-1", "failed"),
    ]);
    renderList();

    expect(await screen.findByText("ride-voice-e2e-3")).toBeInTheDocument();
    expect(screen.getByText("ride-voice-smoke-1")).toBeInTheDocument();
  });

  it("filters by name as you search", async () => {
    const user = userEvent.setup();
    listHarnessJobs.mockResolvedValue([
      job("a", "ride-voice-e2e-3", "understanding_agent"),
      job("b", "billing-chat-agent", "failed"),
    ]);
    renderList();

    await screen.findByText("ride-voice-e2e-3");
    await user.type(
      screen.getByPlaceholderText("Search environments"),
      "billing",
    );

    await waitFor(() =>
      expect(screen.queryByText("ride-voice-e2e-3")).toBeNull(),
    );
    expect(screen.getByText("billing-chat-agent")).toBeInTheDocument();
  });

  it("opens the detail route for the row that was clicked", async () => {
    const user = userEvent.setup();
    listHarnessJobs.mockResolvedValue([
      job("a", "ride-voice-e2e-3", "understanding_agent"),
    ]);
    renderList();

    await user.click(await screen.findByText("ride-voice-e2e-3"));

    expect(navigate).toHaveBeenCalledWith("/dashboard/simulate/harness/a");
  });

  it("offers the create action when nothing has been run yet", async () => {
    listHarnessJobs.mockResolvedValue([]);
    renderList();

    expect(
      await screen.findByText("No RL environments yet"),
    ).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByText("Create RL environment"));
    expect(navigate).toHaveBeenCalledWith("/dashboard/simulate/harness/new");
  });

  it("surfaces a failing list request instead of an empty table", async () => {
    listHarnessJobs.mockRejectedValue({
      response: { data: { detail: "Harness sandbox is unavailable" } },
    });
    renderList();

    expect(
      await screen.findByText("Harness sandbox is unavailable"),
    ).toBeInTheDocument();
  });
});
