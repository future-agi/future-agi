import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  render as baseRender,
  act,
  fireEvent,
  screen,
  waitFor,
} from "src/utils/test-utils";

/* Running the measurable cases from Use with my agent: what its job can come to. */

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("src/api/agents/connectedAgents", () => ({
  useConnectedAgents: () => ({
    data: {
      items: [
        { id: "a1", name: "ShopMate", modality: "text", source: "retell" },
      ],
      total: 1,
    },
    isLoading: false,
  }),
}));

vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: ({ selectedAgentId }) => ({
    data: selectedAgentId
      ? { pages: [{ results: [{ id: "version-1", is_active: true }] }] }
      : undefined,
    isLoading: false,
  }),
}));

const FIT = {
  agent: { name: "ShopMate", version: "v1" },
  agent_tools: [{ name: "order_details" }],
  rows: [{ world: "get_order", agent: "order_details", kind: "mapped" }],
  platform_actions: [],
  total: 2,
  unmeasured: [],
};
vi.mock("src/api/harness/releases", () => ({
  useHarnessReleaseFit: () => ({ data: FIT, isError: false }),
  createHarnessAdaptation: vi.fn(),
}));

let keyCount = 0;
vi.mock("src/api/harness/harness", () => ({
  harnessIdempotencyKey: () => {
    keyCount += 1;
    return `key-${keyCount}`;
  },
}));

const readJob = vi.fn();
vi.mock("src/api/simulate-environments/environment", () => ({
  harnessJobQuery: (id) => ({
    queryKey: ["harness-job", id],
    queryFn: () => readJob(id),
    refetchInterval: () => false,
  }),
}));

const { createHarnessAdaptation } = await import("src/api/harness/releases");
const { default: ReadyEnvironmentPanel } = await import(
  "../starts/ReadyEnvironmentPanel"
);
const { readyEnvironmentFrom } = await import("../readyEnvironments");

const world = readyEnvironmentFrom({
  id: "rel-1",
  digest: "sha256:" + "ab".repeat(32),
  name: "retail",
  system: true,
  parent: null,
  tools: [],
  policy: { parameters: [], agent_rules: [] },
  suites: [{ id: "suite-1", name: "retail_base", scenario_count: 2 }],
  catalogue: null,
});

const jobOf = (state, result = null, failure = null) => ({
  job: { metadata: { adaptation_result: result } },
  status: { state, failure },
});
const RUN = { job_id: "run-1", run_test_id: "rt-1", test_execution_id: "te-1" };

const onBusyChange = vi.fn();
const render = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = baseRender(
    <QueryClientProvider client={client}>
      <ReadyEnvironmentPanel
        item={{ world, initialTab: "start" }}
        onBusyChange={onBusyChange}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("radio", { name: "ShopMate" }));
  return view;
};
const run = () => screen.getByRole("button", { name: /^Run the/ });
const preparing = () =>
  screen.queryByRole("status", { name: "Preparing your run" });
const sentKeys = () =>
  createHarnessAdaptation.mock.calls.map(([body]) => body.idempotency_key);
const pass = (ms) => act(() => vi.advanceTimersByTimeAsync(ms));

beforeEach(() => {
  navigate.mockReset();
  onBusyChange.mockReset();
  readJob.mockReset();
  createHarnessAdaptation.mockReset();
  createHarnessAdaptation.mockResolvedValue({ job: { job_id: "adapt-job" } });
});
afterEach(() => vi.useRealTimers());

describe("preparing a run", () => {
  it("waits for a run recorded just after its adaptation completed", async () => {
    vi.useFakeTimers();
    const done = { composition_id: "env-1" };
    readJob
      .mockResolvedValueOnce(jobOf("completed", done))
      .mockResolvedValue(jobOf("completed", { ...done, run: RUN }));
    render();
    fireEvent.click(run());
    for (let update = 0; update < 3; update += 1) await pass(100);
    expect(readJob).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();

    await pass(2500);

    expect(navigate.mock.calls[0][0]).toMatch(/env-1.*rt-1.*te-1/);
  });

  it("gives up on a run that never starts and opens the environment instead", async () => {
    vi.useFakeTimers();
    readJob.mockResolvedValue(jobOf("completed", { composition_id: "env-1" }));
    render();
    fireEvent.click(run());
    // The job starts, then is read, then its wait is timed: each is an update of its own.
    for (let update = 0; update < 3; update += 1) await pass(100);
    expect(preparing()).toBeInTheDocument();
    await pass(40000);

    expect(preparing()).toBeNull();
    expect(
      screen.getByText(
        "Your environment is ready, but its run didn't start. Open it to run the cases.",
      ),
    ).toBeInTheDocument();
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
    const reads = readJob.mock.calls.length;
    await pass(10000);
    expect(readJob.mock.calls.length).toBe(reads);

    fireEvent.click(screen.getByRole("button", { name: "Open environment" }));
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/env-1",
    );
  });

  it("says why an agent that reaches no tool has no run", async () => {
    readJob.mockResolvedValue(
      jobOf("completed", {
        composition_id: null,
        compose: { message: "the agent reaches no environment tool" },
      }),
    );
    render();
    fireEvent.click(run());

    expect(
      await screen.findByText(
        "Your run can't start: the agent reaches no environment tool",
      ),
    ).toBeInTheDocument();
    expect(preparing()).toBeNull();
    expect(run()).toBeEnabled();
  });

  it("shows a progress read that failed, and can be closed and resumed", async () => {
    readJob.mockRejectedValue({ message: "gateway timeout", statusCode: 504 });
    render();
    fireEvent.click(run());

    expect(
      await screen.findByText(
        "Couldn't read how your run is going: gateway timeout",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(preparing()).toBeNull();
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
    // The same adaptation, not another one.
    fireEvent.click(run());
    await waitFor(() => expect(sentKeys()).toHaveLength(2));
    expect(new Set(sentKeys()).size).toBe(1);
  });
});

describe("while a run is being prepared", () => {
  it("holds the tabs and the choices, and unlocks the drawer when it goes away", async () => {
    readJob.mockResolvedValue(jobOf("running"));
    const { unmount } = render();
    fireEvent.click(run());

    await waitFor(() => expect(preparing()).toBeInTheDocument());
    for (const name of ["World", "Use with my agent", "Customize"])
      expect(screen.getByRole("tab", { name })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "ShopMate" })).toBeDisabled();
    expect(run()).toBeDisabled();
    expect(onBusyChange).toHaveBeenLastCalledWith(true);

    unmount();
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
  });
});

describe("the idempotency key", () => {
  it("is kept for a click whose answer was lost", async () => {
    createHarnessAdaptation.mockRejectedValue({ message: "Network Error" });
    render();
    fireEvent.click(run());
    await screen.findByText("Network Error");
    fireEvent.click(run());

    await waitFor(() => expect(sentKeys()).toHaveLength(2));
    expect(new Set(sentKeys()).size).toBe(1);
  });

  it("changes after the adaptation failed", async () => {
    readJob.mockResolvedValue(
      jobOf("failed", null, { message: "the sandbox was lost" }),
    );
    render();
    fireEvent.click(run());
    await screen.findByText("the sandbox was lost");
    fireEvent.click(run());

    await waitFor(() => expect(sentKeys()).toHaveLength(2));
    expect(new Set(sentKeys()).size).toBe(2);
  });

  it("changes after it was refused for another request", async () => {
    createHarnessAdaptation.mockRejectedValue({
      error: "idempotency_conflict",
      message: "the key was already used",
    });
    render();
    fireEvent.click(run());
    await screen.findByText("the key was already used");
    fireEvent.click(run());

    await waitFor(() => expect(sentKeys()).toHaveLength(2));
    expect(new Set(sentKeys()).size).toBe(2);
  });
});
