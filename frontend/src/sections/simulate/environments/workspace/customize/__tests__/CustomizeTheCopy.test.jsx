import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  fireEvent,
  screen,
  waitFor,
} from "src/utils/test-utils";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("src/api/harness/harness", () => ({ cancelHarnessJob: vi.fn() }));

// The chat is the builder's own console; this page only places it.
vi.mock("../../../buildEnvironment/console/BuilderConsole", () => ({
  default: () => <div>Builder chat</div>,
}));

const { cancelHarnessJob } = await import("src/api/harness/harness");
const { default: CustomizeTheCopy } = await import("../CustomizeTheCopy");

const CHAT = { turns: [], running: false, send: vi.fn() };
const jobOf = (state, extra = {}, failure = null) => ({
  // The job read's shape: details under job, state and failure under status.
  status: { state, failure },
  job: {
    metadata: {
      customization: {
        source: { environment_name: "retail", suite_id: "suite-1" },
        change: "Refunds over $500 need a supervisor",
      },
      ...extra,
    },
  },
});

const render = (job, chat = CHAT) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  baseRender(
    <QueryClientProvider client={client}>
      <CustomizeTheCopy jobId="copy-job" job={job} chat={chat} />
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  navigate.mockReset();
  cancelHarnessJob.mockReset();
  cancelHarnessJob.mockResolvedValue({});
});

describe("The copy's design", () => {
  it("shows the change and where the copy comes from, beside the builder", () => {
    render(jobOf("running"));

    expect(screen.getByText("Retail · copy")).toBeInTheDocument();
    expect(
      screen.getByText("Refunds over $500 need a supervisor"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("The ready world and your change"),
    ).toBeInTheDocument();
    expect(screen.getByText("Builder chat")).toBeInTheDocument();
  });

  it("says what the builder is doing while the job is still provisioning", () => {
    render(jobOf("provisioning"), { ...CHAT, waiting: true });
    expect(screen.getByText("Needs input")).toBeInTheDocument();
  });

  it("shows the job's own ending once it has one", () => {
    render(jobOf("failed"), { ...CHAT, waiting: true });
    expect(screen.queryByText("Needs input")).toBeNull();
  });

  it("opens the finished copy to use with an agent", () => {
    render(
      jobOf("completed", {
        customization_result: {
          environment_release_id: "rel-copy",
          scenario_count: 12,
        },
      }),
    );

    expect(screen.getByText(/Your copy is ready/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel build" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Use with my agent" }));

    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments?build=world%3Arel-copy%3Astart",
    );
  });

  it("says why a copy wasn't made", () => {
    render(
      jobOf(
        "failed",
        {},
        { code: "design_stopped", message: "no such records" },
      ),
    );

    expect(screen.getByText("no such records")).toBeInTheDocument();
  });

  it("stops the build only once it's confirmed, sending no free-text reason", async () => {
    render(jobOf("running"));

    fireEvent.click(screen.getByRole("button", { name: "Cancel build" }));
    expect(cancelHarnessJob).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Stop building" }));

    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(cancelHarnessJob.mock.calls[0]).toEqual(["copy-job"]);
  });
});
