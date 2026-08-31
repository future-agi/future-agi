import { describe, it, expect, vi, beforeEach } from "vitest";
import { Route, Routes } from "react-router-dom";
import { render, renderWithRouter, screen, userEvent, waitFor, within } from "src/utils/test-utils";
import AlEnvironmentView from "../AlEnvironmentView";

const mutation = () => ({ mutate: vi.fn(), isPending: false });

// vi.mock is hoisted above every const in this file, so the mock object has to be
// hoisted with it — otherwise the factory closes over a binding that does not exist yet.
const hooks = vi.hoisted(() => ({
  useAlkStatus: vi.fn(),
  useAlkSessions: vi.fn(),
  useAlkHistory: vi.fn(),
  useAlkContract: vi.fn(),
  useAlkWorld: vi.fn(),
  useAlkScenarios: vi.fn(),
  useAlkSimulations: vi.fn(),
  useAlkSubgoals: vi.fn(),
  useAlkRuns: vi.fn(),
  useAlkSimulation: vi.fn(),
  useCreateAlkSession: vi.fn(),
  useOpenAlkSession: vi.fn(),
  useDeleteAlkSession: vi.fn(),
  useSetAlkStage: vi.fn(),
}));

vi.mock("src/api/al-environment/alEnvironment", () => hooks);

// The conversation hook is mocked as a whole: this suite is about composition, and its own
// behaviour is covered in useAlkConversation.test.jsx.
const conversation = vi.hoisted(() => ({ useAlkConversation: vi.fn() }));
vi.mock("src/api/al-environment/useAlkConversation", () => conversation);

// The snackbar module pulls in the whole notistack provider, which this suite does not mount.
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));

const openStatus = {
  session: { id: "s1" },
  stage: "understand",
  stages: { reception: "", understand: "", build: "needs a contract first", scenarios: "", run: "" },
  agent: "drive_thru",
  model: "claude-sonnet-4-6",
  spent_usd: 0.1234,
  have: { contract: true },
  busy: false,
};

beforeEach(() => {
  hooks.useAlkStatus.mockReturnValue({ status: openStatus, isError: false, refetch: vi.fn() });
  hooks.useAlkSessions.mockReturnValue({ sessions: [{ id: "s1", agent: "drive_thru" }], openSessionId: "s1" });
  hooks.useAlkHistory.mockReturnValue({ messages: [{ role: "tester", text: "Read the agent." }] });
  // Deliberately NOT "drive_thru": that string is also the agent name, so it appears in the
  // session picker and the roadmap too, and getByText would match several elements.
  hooks.useAlkContract.mockReturnValue({ contract: { name: "coffee_contract" } });
  hooks.useAlkWorld.mockReturnValue({ world: { tables: [] } });
  hooks.useAlkScenarios.mockReturnValue({ scenarios: [] });
  hooks.useAlkSimulations.mockReturnValue({ runs: [] });
  hooks.useAlkSubgoals.mockReturnValue({ subgoals: null });
  hooks.useAlkRuns.mockReturnValue({ legacyRuns: [] });
  hooks.useAlkSimulation.mockReturnValue({ run: null });
  [
    "useCreateAlkSession", "useOpenAlkSession", "useDeleteAlkSession", "useSetAlkStage",
  ].forEach((name) => hooks[name].mockReturnValue(mutation()));
  conversation.useAlkConversation.mockReturnValue({
    live: [], streaming: false, error: "",
    say: vi.fn(), runScenarios: vi.fn(), stop: vi.fn(), clearLive: vi.fn(),
  });
});

describe("AlEnvironmentView", () => {
  it("shows the unreachable state when status fails", () => {
    hooks.useAlkStatus.mockReturnValue({ status: null, isError: true, refetch: vi.fn() });
    render(<AlEnvironmentView />);
    expect(screen.getByText(/can't reach the harness/i)).toBeInTheDocument();
  });

  it("draws the roadmap, the readout and the transcript together", () => {
    render(<AlEnvironmentView />);
    expect(screen.getByRole("button", { name: /Contract/ })).toBeInTheDocument();
    expect(screen.getByText(/claude-sonnet-4-6/)).toBeInTheDocument();
    expect(screen.getByText("Read the agent.")).toBeInTheDocument();
  });

  it("opens the Contract tab by default and shows its payload", () => {
    render(<AlEnvironmentView />);
    expect(screen.getByText(/coffee_contract/)).toBeInTheDocument();
  });

  it("switches tabs on request", async () => {
    render(<AlEnvironmentView />);
    await userEvent.click(screen.getByRole("tab", { name: /environment/i }));
    expect(screen.getByText(/not built yet/i)).toBeInTheDocument();
  });

  it("asks the harness to change stage when a reachable stage is clicked", async () => {
    const mutate = vi.fn();
    hooks.useSetAlkStage.mockReturnValue({ mutate, isPending: false });
    render(<AlEnvironmentView />);
    await userEvent.click(screen.getByRole("button", { name: /Scenarios/ }));
    expect(mutate).toHaveBeenCalledWith("scenarios");
  });

  it("surfaces the harness's own words when it refuses a request mid-turn", () => {
    hooks.useSetAlkStage.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      error: { response: { status: 409, data: { error: "still working on the last thing" } } },
    });
    render(<AlEnvironmentView />);
    expect(screen.getByText(/still working on the last thing/)).toBeInTheDocument();
  });

  it("drops the previous conversation when a new session is started", async () => {
    const clearLive = vi.fn();
    const mutate = vi.fn();
    hooks.useCreateAlkSession.mockReturnValue({ mutate, isPending: false });
    conversation.useAlkConversation.mockReturnValue({
      live: [{ role: "you", text: "build the world" }],
      streaming: false, error: "", thinking: "",
      say: vi.fn(), runScenarios: vi.fn(), stop: vi.fn(), clearLive,
    });
    render(<AlEnvironmentView />);
    await userEvent.click(screen.getByRole("button", { name: /^new$/i }));
    // The thread is only cleared once the harness confirms the switch.
    expect(mutate).toHaveBeenCalledTimes(1);
    mutate.mock.calls[0][1].onSuccess();
    expect(clearLive).toHaveBeenCalledTimes(1);
  });

  it("drops the previous conversation when another session is opened", async () => {
    const clearLive = vi.fn();
    const mutate = vi.fn();
    hooks.useDeleteAlkSession.mockReturnValue({ mutate, isPending: false });
    conversation.useAlkConversation.mockReturnValue({
      live: [], streaming: false, error: "", thinking: "",
      say: vi.fn(), runScenarios: vi.fn(), stop: vi.fn(), clearLive,
    });
    render(<AlEnvironmentView />);
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: /^delete$/i })
    );
    mutate.mock.calls[0][1].onSuccess();
    expect(clearLive).toHaveBeenCalledTimes(1);
  });

  it("cannot open the simulation runs until the harness names one", () => {
    render(<AlEnvironmentView />);
    expect(screen.getByRole("button", { name: /view simulations/i })).toBeDisabled();
  });

  it("links to every run of the test, not one execution of it", () => {
    hooks.useAlkStatus.mockReturnValue({
      status: { ...openStatus, run_test_id: "test-uuid", execution_id: "exec-uuid" },
      isError: false,
      refetch: vi.fn(),
    });
    render(<AlEnvironmentView />);
    const link = screen.getByRole("link", { name: /view simulations/i });
    expect(link).toHaveAttribute(
      "href",
      "/dashboard/simulate/test/test-uuid/runs"
    );
  });

  it("does not show a finished turn twice once history has it", () => {
    // History has refetched with the turn the stream just produced; `live` still holds its
    // copy. Showing both is what rendered every completed turn twice.
    hooks.useAlkHistory.mockReturnValue({
      messages: [{ role: "you", text: "build the world" }],
    });
    conversation.useAlkConversation.mockReturnValue({
      live: [{ role: "you", text: "build the world" }],
      streaming: false, error: "", thinking: "",
      say: vi.fn(), runScenarios: vi.fn(), stop: vi.fn(), clearLive: vi.fn(),
    });
    render(<AlEnvironmentView />);
    expect(screen.getAllByText("build the world")).toHaveLength(1);
  });

  it("keeps an error line after the turn ends, since history never has it", () => {
    hooks.useAlkHistory.mockReturnValue({ messages: [] });
    conversation.useAlkConversation.mockReturnValue({
      live: [{ role: "error", text: "the model refused to continue" }],
      streaming: false, error: "", thinking: "",
      say: vi.fn(), runScenarios: vi.fn(), stop: vi.fn(), clearLive: vi.fn(),
    });
    render(<AlEnvironmentView />);
    expect(screen.getByText(/the model refused to continue/)).toBeInTheDocument();
  });

  it("offers a composer so the session can be talked to", () => {
    render(<AlEnvironmentView />);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("shows what is still streaming alongside the stored history", () => {
    conversation.useAlkConversation.mockReturnValue({
      live: [{ role: "you", text: "build the world" }],
      streaming: true, error: "",
      say: vi.fn(), runScenarios: vi.fn(), stop: vi.fn(), clearLive: vi.fn(),
    });
    render(<AlEnvironmentView />);
    expect(screen.getByText("Read the agent.")).toBeInTheDocument();
    expect(screen.getByText("build the world")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
  });

  it("shows a streaming refusal in the thread, beside the turn it interrupted", () => {
    conversation.useAlkConversation.mockReturnValue({
      live: [{ role: "error", text: "still working on the build stage — one moment" }],
      streaming: false, error: "still working on the build stage — one moment",
      say: vi.fn(), runScenarios: vi.fn(), stop: vi.fn(), clearLive: vi.fn(),
    });
    render(<AlEnvironmentView />);
    expect(screen.getByText(/still working on the build stage/)).toBeInTheDocument();
  });

  it("falls back to a readable message when the refusal has no body", () => {
    hooks.useDeleteAlkSession.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      error: { message: "Network Error" },
    });
    render(<AlEnvironmentView />);
    expect(screen.getByText(/Network Error/)).toBeInTheDocument();
  });
});

describe("AlEnvironmentView, addressed by URL", () => {
  // test-utils already supplies a BrowserRouter, so the route is matched inside it rather
  // than nesting a second router.
  const renderAt = (path) =>
    renderWithRouter(
      <Routes>
        <Route path="/rl-environment/:sessionId" element={<AlEnvironmentView />} />
      </Routes>,
      { route: path }
    );

  it("opens the session the URL names", async () => {
    const mutate = vi.fn();
    hooks.useOpenAlkSession.mockReturnValue({ mutate, isPending: false });
    hooks.useAlkStatus.mockReturnValue({
      status: { ...openStatus, session: { id: "other" } },
      isError: false,
      refetch: vi.fn(),
    });
    renderAt("/rl-environment/wanted");
    await waitFor(() => expect(mutate).toHaveBeenCalledWith("wanted", expect.anything()));
  });

  it("leaves the session alone when the URL already names it", async () => {
    const mutate = vi.fn();
    hooks.useOpenAlkSession.mockReturnValue({ mutate, isPending: false });
    hooks.useAlkStatus.mockReturnValue({
      status: { ...openStatus, session: { id: "already" } },
      isError: false,
      refetch: vi.fn(),
    });
    renderAt("/rl-environment/already");
    await waitFor(() => expect(screen.getByRole("textbox")).toBeInTheDocument());
    expect(mutate).not.toHaveBeenCalled();
  });

  it("waits rather than opening while a stage is running", async () => {
    const mutate = vi.fn();
    hooks.useOpenAlkSession.mockReturnValue({ mutate, isPending: false });
    hooks.useAlkStatus.mockReturnValue({
      status: { ...openStatus, busy: true, session: { id: "other" } },
      isError: false,
      refetch: vi.fn(),
    });
    renderAt("/rl-environment/wanted");
    // Says which environment is actually open rather than drawing its work under this URL.
    await waitFor(() =>
      expect(screen.getByText(/other is still running/i)).toBeInTheDocument()
    );
    // Opening mid-turn is refused with a 409, so it must not even be attempted.
    expect(mutate).not.toHaveBeenCalled();
  });

  it("sends you back to the list when the URL names an environment that is gone", async () => {
    const mutate = vi.fn((id, opts) => opts.onError({ response: { status: 404 } }));
    hooks.useOpenAlkSession.mockReturnValue({ mutate, isPending: false });
    hooks.useAlkStatus.mockReturnValue({
      status: { ...openStatus, session: { id: "other" } },
      isError: false,
      refetch: vi.fn(),
    });
    renderAt("/rl-environment/ghost");
    await waitFor(() => expect(mutate).toHaveBeenCalledWith("ghost", expect.anything()));
    // One attempt only: retrying would repaint the page in a loop.
    expect(mutate).toHaveBeenCalledTimes(1);
    expect(window.location.pathname).toBe("/dashboard/simulate/rl-environment");
  });

  it("stays put when opening is refused rather than failing", async () => {
    // A 409 means a stage is running, not that the environment is bad. Bouncing here would
    // throw the operator off a perfectly valid URL.
    const mutate = vi.fn((id, opts) =>
      opts.onError({ response: { status: 409, data: { error: "still working on the last thing" } } })
    );
    hooks.useOpenAlkSession.mockReturnValue({ mutate, isPending: false });
    hooks.useAlkStatus.mockReturnValue({
      status: { ...openStatus, session: { id: "other" } },
      isError: false,
      refetch: vi.fn(),
    });
    renderAt("/rl-environment/wanted");
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(window.location.pathname).toBe("/rl-environment/wanted");
  });

  it("does not open a second session while one is already in flight", async () => {
    // The harness takes one request at a time; a concurrent open comes back 409 and would
    // look like the environment was bad.
    const mutate = vi.fn(); // never settles
    hooks.useOpenAlkSession.mockReturnValue({ mutate, isPending: false });
    hooks.useAlkStatus.mockReturnValue({
      status: { ...openStatus, session: { id: "other" } },
      isError: false,
      refetch: vi.fn(),
    });
    const { rerender } = renderAt("/rl-environment/first");
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    // A status refetch re-renders while the first open is still outstanding.
    hooks.useAlkStatus.mockReturnValue({
      status: { ...openStatus, session: { id: "other" }, spent_usd: 0.2 },
      isError: false,
      refetch: vi.fn(),
    });
    rerender(
      <Routes>
        <Route path="/rl-environment/:sessionId" element={<AlEnvironmentView />} />
      </Routes>
    );
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
  });

  it("does not re-open a session it has just created", async () => {
    // Creating makes the new session current and seeds status with it, so arriving at its URL
    // finds it already open. Opening again would be a wasted request the harness may refuse.
    const open = vi.fn();
    hooks.useOpenAlkSession.mockReturnValue({ mutate: open, isPending: false });
    hooks.useAlkStatus.mockReturnValue({
      status: { ...openStatus, session: { id: "fresh" } },
      isError: false,
      refetch: vi.fn(),
    });
    renderAt("/rl-environment/fresh");
    await waitFor(() => expect(screen.getByRole("textbox")).toBeInTheDocument());
    expect(open).not.toHaveBeenCalled();
  });

  it("offers a way back to the list", () => {
    renderAt("/rl-environment/s1");
    expect(screen.getByRole("link", { name: /all environments/i })).toHaveAttribute(
      "href",
      "/dashboard/simulate/rl-environment"
    );
  });
});
