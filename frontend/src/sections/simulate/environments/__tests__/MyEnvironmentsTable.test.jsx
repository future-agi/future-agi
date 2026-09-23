import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("src/api/harness/harness", () => ({
  getHarnessJob: vi.fn(),
}));
vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: vi.fn(),
  deleteHarnessEnvironment: vi.fn(),
}));

const { getHarnessJob } = await import("src/api/harness/harness");
const { listHarnessEnvironments, deleteHarnessEnvironment } = await import(
  "src/api/simulate-environments/harnessEnvironments"
);
const { default: MyEnvironmentsTab } = await import("../MyEnvironmentsTab");

// The job detail the run action fetches: platform ids sit at the top level, so
// runSimulationTarget routes to the product's execution detail.
const JOB_DETAIL = {
  job: { job_id: "job-support", metadata: { name: "Customer Support Line" } },
  status: { stage: "completed", created_at: "2026-09-15T09:00:00Z" },
  credentials: { detected_connectors: ["livekit"] },
  platform: { run_test_id: "rt-support", test_execution_id: "ex-support" },
  stage_outputs: [],
};

// A small harness-environments page: the four pill states, two voice rows and
// two chat rows, with a real description on the first row.
const HARNESS_ENVS = {
  count: 4,
  next: null,
  previous: null,
  total_pages: 1,
  current_page: 1,
  results: [
    {
      id: "job-support",
      name: "Customer Support Line",
      description: "Handles inbound billing calls",
      domain: "Logistics",
      source_kind: "provider",
      agent_type: "voice",
      status: "completed",
      stage: "completed",
      scenario_count: 8,
      sub_goals_count: 12,
      tools_count: 3,
      runs_count: 1,
      last_updated: "2026-09-15T09:00:00Z",
      created_at: "2026-09-10T09:00:00Z",
    },
    {
      id: "job-billing",
      name: "Billing Chat Agent",
      description: null,
      source_kind: "github",
      agent_type: "chat",
      status: "running",
      stage: "running",
      scenario_count: 0,
      tools_count: null,
      last_updated: "2026-09-15T11:59:50Z",
      created_at: "2026-09-14T09:00:00Z",
    },
    {
      id: "job-triage",
      name: "Repo Triage Bot",
      description: null,
      source_kind: "github",
      agent_type: "chat",
      status: "building",
      stage: "queued",
      scenario_count: 0,
      tools_count: null,
      last_updated: "2026-09-14T12:00:00Z",
      created_at: "2026-09-14T10:00:00Z",
    },
    {
      id: "job-airline",
      name: "Airline Rebooking",
      description: null,
      source_kind: "provider",
      agent_type: "voice",
      status: "failed",
      stage: "failed",
      scenario_count: 0,
      tools_count: null,
      last_updated: "2026-08-01T12:00:00Z",
      created_at: "2026-07-01T09:00:00Z",
    },
  ],
};

const renderTab = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MyEnvironmentsTab />
    </QueryClientProvider>,
  );
};

const rowFor = (name) => screen.getByText(name).closest('[role="row"]');

const openMenu = async (user, name) => {
  await user.click(
    within(rowFor(name)).getByRole("button", { name: "Row actions" }),
  );
};

describe("MyEnvironmentsTable", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-15T12:00:00Z"));
    navigate.mockReset();
    listHarnessEnvironments.mockReset();
    listHarnessEnvironments.mockResolvedValue(HARNESS_ENVS);
    deleteHarnessEnvironment.mockReset();
    deleteHarnessEnvironment.mockResolvedValue(undefined);
    getHarnessJob.mockReset();
    getHarnessJob.mockResolvedValue(JOB_DETAIL);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("maps every harness job to a row with a relative updated time", async () => {
    renderTab();

    expect(await screen.findByText("Customer Support Line")).toBeInTheDocument();
    ["Billing Chat Agent", "Repo Triage Bot", "Airline Rebooking"].forEach(
      (name) => expect(screen.getByText(name)).toBeInTheDocument(),
    );

    expect(
      within(rowFor("Customer Support Line")).getByText("3 hours ago"),
    ).toBeInTheDocument();
    expect(
      within(rowFor("Billing Chat Agent")).getByText("just now"),
    ).toBeInTheDocument();
    expect(
      within(rowFor("Airline Rebooking")).getByText("1 month ago"),
    ).toBeInTheDocument();
  });

  it("maps each stage to its status pill", async () => {
    renderTab();
    await screen.findByText("Customer Support Line");

    ["Completed", "Running…", "Building", "Failed"].forEach((label) =>
      expect(screen.getByText(label)).toBeInTheDocument(),
    );
  });

  it("maps connectors to voice / chat agent-type cells", async () => {
    renderTab();
    await screen.findByText("Customer Support Line");

    // livekit + vapi → Voice; http + none detected → Chat.
    expect(screen.getAllByText("Voice")).toHaveLength(2);
    expect(screen.getAllByText("Chat")).toHaveLength(2);
  });

  it("renders every column from the real §1 list contract, no dummy headers", async () => {
    renderTab();
    await screen.findByText("Customer Support Line");

    // Every column now maps to a real §1 field, so no "Dummy" header pill remains.
    expect(screen.queryByText("Dummy")).toBeNull();

    const row = within(rowFor("Customer Support Line"));
    // Real description, domain, sub-goals count and run state for the first row.
    expect(row.getByText("Handles inbound billing calls")).toBeInTheDocument();
    expect(row.getByText("Logistics")).toBeInTheDocument();
    expect(row.getByText("12")).toBeInTheDocument();
    // runs_count 1 → the Runs column shows the number.
    expect(row.getByText("1")).toBeInTheDocument();
  });

  it("renders dashes and the never-run state when the §1 additions are absent", async () => {
    renderTab();
    await screen.findByText("Repo Triage Bot");

    // A row the backend has not authored these fields for: domain and sub-goals
    // fall back to a dash, and a null runs_count reads as a dash too.
    const row = within(rowFor("Repo Triage Bot"));
    expect(row.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("shows the empty state when the environments list resolves empty", async () => {
    listHarnessEnvironments.mockResolvedValue({ results: [] });
    renderTab();

    expect(await screen.findByText("No environments yet")).toBeInTheDocument();
    expect(screen.queryByText("Dummy")).toBeNull();
  });

  it("offers Open + Re-run + Delete on a completed environment that has run", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");

    const menu = screen.getByRole("menu");
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(3);
    expect(within(menu).getByText("Open")).toBeInTheDocument();
    // §1 runs_count 1 means this environment has run, so the run action reads
    // "Re-run simulation" (RowActionsMenu switches on runsTotal > 0).
    expect(
      within(menu).getByRole("menuitem", { name: /Re-run simulation/ }),
    ).toBeInTheDocument();
    expect(within(menu).getByText("Delete")).toBeInTheDocument();

    await user.click(within(menu).getByText("Open"));
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/job-support",
    );
  });

  it("fetches the job then routes to the run target on Re-run simulation", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");
    await user.click(
      within(screen.getByRole("menu")).getByText("Re-run simulation"),
    );

    // The list payload has no platform, so the row action fetches the detail
    // first, then navigates to the product's execution target.
    await waitFor(() =>
      expect(getHarnessJob).toHaveBeenCalledWith("job-support"),
    );
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(
        "/dashboard/simulate/test/rt-support/ex-support/call-details",
      ),
    );
  });

  it("falls back to the product run entry when the job fetch fails", async () => {
    getHarnessJob.mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");
    await user.click(
      within(screen.getByRole("menu")).getByText("Re-run simulation"),
    );

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("/dashboard/simulate/test"),
    );
  });

  it("disables the run action while the environment is building", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Repo Triage Bot");

    await openMenu(user, "Repo Triage Bot");

    expect(
      within(screen.getByRole("menu"))
        .getByText("Run simulation")
        .closest('[role="menuitem"]'),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("drives server pagination: shows the server total and fetches the next page", async () => {
    // A total larger than one page: only the first page of rows comes back, but
    // the pager is driven by the server count.
    listHarnessEnvironments.mockResolvedValue({
      ...HARNESS_ENVS,
      count: 30,
      total_pages: 2,
    });
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    // The footer reflects the server total, not the four rows on screen.
    expect(screen.getByText(/1–25 of 30/)).toBeInTheDocument();
    expect(listHarnessEnvironments).toHaveBeenCalledWith({ page: 1, limit: 25 });

    // The next chevron is the last button in the tab; clicking it asks the
    // backend for the second page.
    const buttons = screen.getAllByRole("button");
    await user.click(buttons[buttons.length - 1]);

    await waitFor(() =>
      expect(listHarnessEnvironments).toHaveBeenCalledWith({ page: 2, limit: 25 }),
    );
  });

  it("removes the environment after confirming delete", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");
    await user.click(within(screen.getByRole("menu")).getByText("Delete"));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Delete environment?")).toBeInTheDocument();
    expect(within(dialog).getByText("Customer Support Line")).toBeInTheDocument();

    // Delete invalidates the list, so the refetched page no longer carries the
    // removed row.
    deleteHarnessEnvironment.mockResolvedValueOnce(undefined);
    listHarnessEnvironments.mockResolvedValue({
      ...HARNESS_ENVS,
      count: 3,
      results: HARNESS_ENVS.results.filter((r) => r.id !== "job-support"),
    });

    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(deleteHarnessEnvironment).toHaveBeenCalledWith("job-support");
    await waitFor(() =>
      expect(screen.queryByText("Customer Support Line")).toBeNull(),
    );
    ["Billing Chat Agent", "Repo Triage Bot", "Airline Rebooking"].forEach(
      (name) => expect(screen.getByText(name)).toBeInTheDocument(),
    );
  });

  it("keeps the environment when delete is cancelled", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");
    await user.click(within(screen.getByRole("menu")).getByText("Delete"));

    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }),
    );

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("Customer Support Line")).toBeInTheDocument();
  });

  it("navigates to the detail route on row click", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await user.click(screen.getByText("Customer Support Line"));

    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/job-support",
    );
  });
});
