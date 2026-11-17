import React from "react";
import { act, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OmegaError } from "src/sections/omega/shared/omegaStates";

import {
  CAPS,
  PROVIDER,
  fakeApi,
  renderSandboxes,
  sandboxView,
} from "./helpers";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: SandboxesListPage } = await import("../SandboxesListPage");

/** EE's 503 when the sandbox machine is out, as the Omega client reads it. */
const machineOut = (reason) =>
  new OmegaError({
    state: "unavailable",
    status: 503,
    code: "sandboxes_unavailable",
    reason,
    message: "Sandboxes are unavailable right now. Try again shortly.",
  });

const advance = (ms) => act(() => vi.advanceTimersByTimeAsync(ms));

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Sandbox reads when the sandbox machine is out", () => {
  it("the page asks again on its own when the sandbox machine could not be reached", async () => {
    const list = vi
      .fn()
      .mockRejectedValueOnce(machineOut("sandbox_host_unreachable"))
      .mockRejectedValueOnce(machineOut("sandbox_host_failed"))
      .mockResolvedValue({
        sandboxes: [sandboxView()],
        caps: CAPS,
        provider: PROVIDER,
      });
    renderSandboxes(<SandboxesListPage />, { api: fakeApi({ list }) });
    await advance(5_000);
    expect(list).toHaveBeenCalledTimes(3);
    expect(screen.getAllByTestId("sandbox-row")).toHaveLength(1);
    expect(screen.queryByTestId("sandbox-error-state")).toBeNull();
  });
});
