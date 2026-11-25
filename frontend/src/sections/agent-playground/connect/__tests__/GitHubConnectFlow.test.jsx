import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  renderWithRouter,
  screen,
  within,
} from "src/utils/test-utils";
import GitHubConnectFlow from "../GitHubConnectFlow";

const mockConnect = vi.fn();
const mockStartScan = vi.fn();
const scanState = vi.hoisted(() => ({
  data: null,
  isError: false,
  error: null,
  refetch: () => {},
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock("src/api/integrations", () => ({
  useGitHubConnection: () => ({ data: null, isLoading: false, isError: false }),
  useSourceRepositories: () => ({ data: [], isFetching: false }),
  useSourceBranches: () => ({ data: [], isFetching: false }),
}));

vi.mock("src/api/agents/agentConnect", () => ({
  useStartScan: () => ({ mutate: mockStartScan, isPending: false }),
  useAgentScan: () => ({ ...scanState }),
  useConnectAgent: () => ({ mutate: mockConnect, isPending: false }),
}));

const checkState = vi.hoisted(() => ({
  ready: true,
  status: "idle",
  result: undefined,
  error: null,
  run: vi.fn(),
  reset: vi.fn(),
}));

vi.mock("../useConnectPreflight", () => ({ default: () => checkState }));

vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));

vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Member" }),
}));

const DRAFT_KEY = "agents.connect.github.draft";
const SCANNED = {
  kind: "github",
  repository: "acme/one",
  visibility: "public",
};
const TWO_AGENTS = {
  status: "found",
  commitSha: null,
  agents: [
    { name: "x", subfolder: "", entry: "x.py", envNames: ["X_KEY"] },
    { name: "y", subfolder: "", entry: "y.py", envNames: ["Y_KEY"] },
  ],
};

const startAt = (draft) => {
  sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  renderWithRouter(<GitHubConnectFlow />);
};

const pick = (name) =>
  fireEvent.click(
    within(screen.getByTestId(`found-agent-${name}`)).getByRole("radio"),
  );

const next = () =>
  fireEvent.click(screen.getByRole("button", { name: /^Next/ }));

const back = () =>
  fireEvent.click(screen.getByRole("button", { name: /^Back/ }));

const connectBody = () => {
  fireEvent.click(screen.getByRole("button", { name: /^Connect agent/ }));
  return mockConnect.mock.calls[0][0];
};

beforeEach(() => {
  sessionStorage.clear();
  checkState.ready = true;
  checkState.run.mockReset();
  mockConnect.mockReset();
  mockStartScan.mockReset();
  scanState.data = null;
});

describe("GitHubConnectFlow", () => {
  it("drops a scan of a repository that was changed while it ran", () => {
    startAt({ mode: "link", link: "acme/one", stage: "source" });
    fireEvent.click(screen.getByRole("button", { name: "Find agents" }));
    const [scanned, callbacks] = mockStartScan.mock.calls[0];
    expect(scanned.repository).toBe("acme/one");

    fireEvent.change(
      screen.getByPlaceholderText("https://github.com/owner/repo"),
      { target: { value: "acme/two" } },
    );
    act(() => callbacks.onSuccess({ id: "scan-1" }));

    expect(screen.getByRole("button", { name: "Find agents" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /^Next/ })).toBeNull();
  });

  it("connects the repository that was scanned, not the one typed since", () => {
    scanState.data = TWO_AGENTS;
    startAt({
      mode: "link",
      link: "acme/two",
      stage: "agents",
      scanId: "scan-1",
      scanSource: SCANNED,
    });

    expect(screen.getByText("acme/one")).toBeInTheDocument();
    pick("x");
    next();

    expect(connectBody()).toMatchObject({
      repository: "acme/one",
      entry: "x.py",
    });
  });

  it("connects only after preflight passes, checking the values typed", () => {
    scanState.data = TWO_AGENTS;
    startAt({
      mode: "link",
      link: "acme/one",
      stage: "agents",
      scanId: "scan-1",
      scanSource: SCANNED,
    });
    checkState.ready = false;
    pick("x");
    next();
    fireEvent.change(screen.getByTestId("env-value-X_KEY"), {
      target: { value: "secret-x" },
    });

    expect(
      screen.getByRole("button", { name: /^Connect agent/ }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
    expect(checkState.run).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "repo",
        value: "acme/one",
        entry: "x.py",
        envValues: { X_KEY: "secret-x" },
      }),
    );
  });

  it("keeps the values and name of one agent away from another", () => {
    scanState.data = TWO_AGENTS;
    startAt({
      mode: "link",
      link: "acme/one",
      stage: "agents",
      scanId: "scan-1",
      scanSource: SCANNED,
    });

    pick("x");
    next();
    expect(screen.getByPlaceholderText("Support agent")).toHaveValue("x");
    fireEvent.change(screen.getByTestId("env-value-X_KEY"), {
      target: { value: "secret-x" },
    });
    fireEvent.change(screen.getByTestId("env-name-draft"), {
      target: { value: "SHARED" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add a value/ }));
    fireEvent.change(screen.getByTestId("env-value-SHARED"), {
      target: { value: "secret-shared" },
    });
    back();
    pick("y");
    next();

    expect(screen.queryByTestId("env-value-X_KEY")).toBeNull();
    expect(screen.queryByTestId("env-value-SHARED")).toBeNull();
    expect(screen.getByPlaceholderText("Support agent")).toHaveValue("y");
    fireEvent.change(screen.getByTestId("env-value-Y_KEY"), {
      target: { value: "secret-y" },
    });
    const body = connectBody();
    expect(body.env_values).toEqual({ Y_KEY: "secret-y" });
    expect(body).toMatchObject({ name: "y", entry: "y.py" });
  });
});
