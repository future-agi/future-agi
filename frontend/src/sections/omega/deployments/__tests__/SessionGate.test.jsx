import React from "react";
import { act, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderDeployments } from "./helpers";
import { workspace } from "./session-mock";

// The real session hook, driven by a probe each test scripts.
const probe = vi.fn();
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useOmegaSession: () => actual.useOmegaSession({ probe }),
  };
});

const { default: SessionGate } = await import("../components/SessionGate");

const THROTTLED = {
  state: "unavailable",
  code: "session_exchange_throttled",
  title: "Too many sign-ins",
  message:
    "Your account opened Omega too many times in the last minute. Try again in a few seconds.",
  retryAfter: null,
};
const READY = { state: "ready", code: null, message: null };

const advance = (ms) => act(() => vi.advanceTimersByTimeAsync(ms));

function renderGate() {
  renderDeployments(
    <SessionGate>
      <div>the tab</div>
    </SessionGate>,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  probe.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SessionGate under a rate limit", () => {
  it("says so at once, asks again by itself after 15 s, and opens the tab when the limit clears", async () => {
    let release;
    probe
      .mockResolvedValueOnce(THROTTLED)
      .mockImplementationOnce(() => new Promise((r) => (release = r)));
    renderGate();
    await advance(0);
    expect(screen.getByText("Omega isn't responding")).toBeInTheDocument();
    expect(screen.getByText(THROTTLED.message)).toBeInTheDocument();
    expect(probe).toHaveBeenCalledTimes(1);

    await advance(14_900);
    expect(probe).toHaveBeenCalledTimes(1);
    await advance(200);
    expect(probe).toHaveBeenCalledTimes(2);
    // While the second probe runs, the refusal stays up: no skeleton flash.
    expect(screen.getByText(THROTTLED.message)).toBeInTheDocument();
    expect(screen.queryByTestId("deployment-loading")).toBeNull();

    await act(async () => release(READY));
    expect(screen.getByText("the tab")).toBeInTheDocument();
    expect(screen.queryByText(THROTTLED.message)).toBeNull();
  });

  it("waits longer while the limit holds and honours Retry-After", async () => {
    probe
      .mockResolvedValueOnce(THROTTLED)
      .mockResolvedValueOnce(THROTTLED)
      .mockResolvedValueOnce({ ...THROTTLED, retryAfter: 40 })
      .mockResolvedValue(READY);
    renderGate();
    await advance(0);
    await advance(15_000);
    expect(probe).toHaveBeenCalledTimes(2);
    // The second answer in a row: its 10 s backoff is under the 15 s floor.
    await advance(15_000);
    expect(probe).toHaveBeenCalledTimes(3);
    // The third asked for 40 s, more than its 20 s backoff: Retry-After wins.
    await advance(39_000);
    expect(probe).toHaveBeenCalledTimes(3);
    await advance(1_000);
    expect(probe).toHaveBeenCalledTimes(4);
    expect(screen.getByText("the tab")).toBeInTheDocument();
  });

  it("leaves any other refusal to the person", async () => {
    probe.mockResolvedValue({
      state: "refused",
      code: "omega_viewer_not_admitted",
      message: "Viewers cannot use Omega.",
    });
    renderGate();
    await advance(0);
    expect(screen.getByText("Viewers cannot use Omega.")).toBeInTheDocument();
    await advance(120_000);
    expect(probe).toHaveBeenCalledTimes(1);
  });
});
