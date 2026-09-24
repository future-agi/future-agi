import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { RUN_SIMULATION_COPY } from "../environmentOptions";

const enqueueSnackbar = vi.fn();
const navigate = vi.fn();

vi.mock("notistack", () => ({ enqueueSnackbar: (...a) => enqueueSnackbar(...a) }));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("src/api/harness/harness", () => ({ listHarnessJobs: vi.fn() }));

const { listHarnessJobs } = await import("src/api/harness/harness");
const { default: MyEnvironmentsTab } = await import("../MyEnvironmentsTab");

// A small harness-jobs payload: two stages beyond the terminal ones, a voice
// connector (livekit/vapi) and a plain chat connector (http).
const HARNESS_JOBS = [
  {
    job: { job_id: "job-support", metadata: { name: "Customer Support Line" } },
    status: { stage: "completed", updated_at: "2026-09-15T09:00:00Z" },
    credentials: { detected_connectors: ["livekit"] },
  },
  {
    job: { job_id: "job-billing", metadata: { name: "Billing Chat Agent" } },
    status: { stage: "running", updated_at: "2026-09-15T11:59:50Z" },
    credentials: { detected_connectors: ["http"] },
  },
  {
    job: { job_id: "job-triage", metadata: { name: "Repo Triage Bot" } },
    status: { stage: "queued", updated_at: "2026-09-14T12:00:00Z" },
    credentials: { detected_connectors: [] },
  },
  {
    job: { job_id: "job-airline", metadata: { name: "Airline Rebooking" } },
    status: { stage: "failed", updated_at: "2026-08-01T12:00:00Z" },
    credentials: { detected_connectors: ["vapi"] },
  },
];

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
    enqueueSnackbar.mockReset();
    navigate.mockReset();
    listHarnessJobs.mockReset();
    listHarnessJobs.mockResolvedValue(HARNESS_JOBS);
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
      within(rowFor("Billing Chat Agent")).getByText("10 seconds ago"),
    ).toBeInTheDocument();
    expect(
      within(rowFor("Airline Rebooking")).getByText("2 months ago"),
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

  it("marks the columns the harness API cannot fill with a dummy header pill", async () => {
    renderTab();
    await screen.findByText("Customer Support Line");

    // Description, Tools, Scenarios, Sub-goals, Runs.
    expect(screen.getAllByText("Dummy")).toHaveLength(5);
    // The placeholder cells render a dash rather than a value.
    expect(
      within(rowFor("Customer Support Line")).getAllByText("—").length,
    ).toBeGreaterThan(0);
  });

  it("shows the empty state when the harness list resolves empty", async () => {
    listHarnessJobs.mockResolvedValue([]);
    renderTab();

    expect(await screen.findByText("No environments yet")).toBeInTheDocument();
    expect(screen.queryByText("Dummy")).toBeNull();
  });

  it("shows an error state, not the empty state, when the list request fails", async () => {
    listHarnessJobs.mockRejectedValue(new Error("harness unavailable"));
    renderTab();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "harness unavailable",
    );
    expect(screen.queryByText("No environments yet")).toBeNull();
  });

  it("offers Open + Run + Delete on a completed environment", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");

    const menu = screen.getByRole("menu");
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(3);
    expect(within(menu).getByText("Open")).toBeInTheDocument();
    // Every harness row starts at runsTotal 0, so the run action reads "Run
    // simulation", never "Re-run".
    expect(
      within(menu).getByRole("menuitem", { name: /Run simulation/ }),
    ).toBeInTheDocument();
    expect(within(menu).getByText("Delete")).toBeInTheDocument();

    await user.click(within(menu).getByText("Open"));
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/job-support",
    );
  });

  it("queues a run and snackbars when Run simulation is chosen", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");
    await user.click(
      within(screen.getByRole("menu")).getByText("Run simulation"),
    );

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(RUN_SIMULATION_COPY),
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

  it("removes the environment after confirming delete", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");
    await user.click(within(screen.getByRole("menu")).getByText("Delete"));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Delete environment?")).toBeInTheDocument();
    expect(within(dialog).getByText("Customer Support Line")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

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
