import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => enqueueSnackbar(...args),
}));

// The versions query is `state.versions` (an API answer), else loading.
const state = {};
vi.mock("src/api/simulate-environments/versions", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useEnvironmentVersions: () => ({
      data: state.versions ? actual.toVersions(state.versions) : undefined,
      error: state.error ?? null,
    }),
  };
});

const { default: AgentMoveBanner } = await import("../AgentMoveBanner");

const ENV = { id: "env-1", versioning: { enabled: true } };
const agent = (overrides) => ({
  id: "a1",
  label: "v1",
  status: "active",
  is_default: false,
  preflight_status: "passed",
  ...overrides,
});
const versions = (next) => ({
  agent: [
    agent({ id: "a2", label: "v2", ...next }),
    agent({ is_default: true }),
  ],
  environment: [],
  suite: [],
});

const renderBanner = (props = {}) =>
  render(
    <AgentMoveBanner env={ENV} onOpenAgentVersions={vi.fn()} {...props} />,
  );

describe("AgentMoveBanner", () => {
  beforeEach(() => {
    state.versions = undefined;
    state.error = undefined;
    enqueueSnackbar.mockReset();
    window.sessionStorage.clear();
  });
  afterEach(() => vi.useRealTimers());

  it("says which agent version is being checked and that the current one stays the default", () => {
    state.versions = versions({ activation_outcome: "validating" });
    renderBanner();
    expect(
      screen.getByText("Checking v2 with this environment"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("v1 stays the default while it's checked."),
    ).toBeInTheDocument();
  });

  it("says when it became the default, once", async () => {
    state.versions = versions({ activation_outcome: "validating" });
    const { rerender } = renderBanner();
    state.versions = {
      ...versions({ is_default: true, activation_outcome: "activated" }),
      agent: [
        agent({ id: "a2", label: "v2", is_default: true }),
        agent({ is_default: false }),
      ],
    };
    rerender(<AgentMoveBanner env={ENV} onOpenAgentVersions={vi.fn()} />);
    rerender(<AgentMoveBanner env={ENV} onOpenAgentVersions={vi.fn()} />);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar.mock.calls[0][0]).toBe(
      "v2 is now the default agent",
    );
    await waitFor(() =>
      expect(
        screen.queryByText("Checking v2 with this environment"),
      ).not.toBeInTheDocument(),
    );
  });

  it("keeps a failed check on screen with its reason until dismissed", async () => {
    const user = userEvent.setup();
    const onOpenAgentVersions = vi.fn();
    state.versions = versions({ activation_outcome: "validating" });
    const { rerender } = renderBanner({ onOpenAgentVersions });
    state.versions = versions({ activation_outcome: "environment_busy" });
    rerender(
      <AgentMoveBanner env={ENV} onOpenAgentVersions={onOpenAgentVersions} />,
    );
    expect(
      screen.getByText("v2: Not made default: the environment was busy"),
    ).toBeInTheDocument();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "Open agent versions" }),
    );
    expect(onOpenAgentVersions).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(
      screen.queryByText(/Not made default: the environment was busy/),
    ).not.toBeInTheDocument();
  });

  it("shows a failed move found on load until it's dismissed for the session", async () => {
    const user = userEvent.setup();
    state.versions = versions({ activation_outcome: "pair_failed" });
    const first = renderBanner();
    expect(
      screen.getByText(
        "v2: Not made default: it failed its check with this environment",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    first.unmount();
    const { rerender } = renderBanner();
    expect(screen.queryByText(/failed its check/)).not.toBeInTheDocument();

    // Tried again and failed the same way: the old dismissal doesn't hide it.
    state.versions = versions({ activation_outcome: "validating" });
    rerender(<AgentMoveBanner env={ENV} onOpenAgentVersions={vi.fn()} />);
    state.versions = versions({ activation_outcome: "pair_failed" });
    rerender(<AgentMoveBanner env={ENV} onOpenAgentVersions={vi.fn()} />);
    expect(screen.getByText(/failed its check/)).toBeInTheDocument();
  });

  it("stays quiet about a move that ended superseded", async () => {
    state.versions = versions({ activation_outcome: "validating" });
    const { rerender, container } = renderBanner();
    state.versions = versions({ activation_outcome: "superseded" });
    rerender(<AgentMoveBanner env={ENV} onOpenAgentVersions={vi.fn()} />);
    await waitFor(() => expect(container).not.toHaveTextContent(/v2/));
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("says a long check is taking longer than usual", () => {
    vi.useFakeTimers();
    state.versions = versions({ activation_outcome: "validating" });
    renderBanner();
    expect(
      screen.queryByText(/This is taking longer than usual\./),
    ).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(20 * 60 * 1000 + 1));
    expect(
      screen.getByText(/This is taking longer than usual\./),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open agent versions" }),
    ).toBeInTheDocument();
  });

  it("keeps the last answer when a poll fails, and shows nothing before the first answer", () => {
    const { container, rerender } = renderBanner();
    expect(container).toBeEmptyDOMElement();
    state.versions = versions({ activation_outcome: "validating" });
    state.error = { statusCode: 500 };
    rerender(<AgentMoveBanner env={ENV} onOpenAgentVersions={vi.fn()} />);
    expect(
      screen.getByText("Checking v2 with this environment"),
    ).toBeInTheDocument();
  });
});
