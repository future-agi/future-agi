import React from "react";
import PropTypes from "prop-types";
import { act, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeApi, renderDeployments } from "./helpers";
import { workspace } from "./session-mock";

vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));

const { useAddSecret } = await import("../secrets/useSecrets");

/** Hands the hook to the test, mounted the way the dialogs mount it. */
function Probe({ onHook }) {
  onHook(useAddSecret());
  return null;
}
Probe.propTypes = { onHook: PropTypes.func.isRequired };

function mountAdd(api) {
  const hook = { current: null };
  renderDeployments(<Probe onHook={(h) => (hook.current = h)} />, {
    api,
    route: "/dashboard/deployments",
  });
  return hook;
}

const ENTRY = { name: "A_KEY", kind: "api_key", value: "v" };

beforeEach(() => {
  workspace.currentWorkspaceId = "ws-a";
});

describe("vault writes keep one Idempotency-Key per exact request", () => {
  it("two sends of one add that overlap the fresh read share one read and one key", async () => {
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const api = fakeApi({
      credentials: vi.fn(async () => {
        await gate;
        return [];
      }),
      createCredential: vi.fn(async ({ name }) => ({ name })),
    });
    const hook = mountAdd(api);
    let both;
    act(() => {
      both = Promise.all([
        hook.current.mutateAsync(ENTRY),
        hook.current.mutateAsync({ ...ENTRY }),
      ]);
    });
    await act(async () => {
      release();
      await both;
    });
    expect(api.credentials).toHaveBeenCalledTimes(1);
    expect(api.createCredential).toHaveBeenCalledTimes(2);
    const [[, first], [, second]] = api.createCredential.mock.calls;
    expect(first.idempotencyKey).toBe(second.idempotencyKey);
  });

  it("a refused add sends nothing and keeps no key: the next save reads the vault again", async () => {
    const api = fakeApi({
      credentials: vi
        .fn()
        .mockResolvedValueOnce([{ name: "A_KEY", status: "active" }])
        .mockResolvedValue([]),
      createCredential: vi.fn(async ({ name }) => ({ name })),
    });
    const hook = mountAdd(api);
    await act(async () => {
      await expect(hook.current.mutateAsync(ENTRY)).rejects.toMatchObject({
        status: 409,
        code: "name_taken",
      });
    });
    expect(api.createCredential).not.toHaveBeenCalled();
    await act(async () => {
      await hook.current.mutateAsync(ENTRY);
    });
    await waitFor(() => expect(api.createCredential).toHaveBeenCalledTimes(1));
    // One read per new request (no list is mounted here, so success re-reads nothing).
    expect(api.credentials).toHaveBeenCalledTimes(2);
  });
});
