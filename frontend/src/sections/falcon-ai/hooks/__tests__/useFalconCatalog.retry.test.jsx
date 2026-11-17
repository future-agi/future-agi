import { StrictMode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import client from "src/utils/axios";
import useFalconCatalog from "../useFalconCatalog";
import { listSkills, fetchConnectors } from "../useFalconAPI";
import useFalconStore from "../../store/useFalconStore";

const h = vi.hoisted(() => ({ workspace: null }));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => h.workspace,
}));
vi.mock("src/utils/axios", () => ({
  default: { defaults: { headers: { common: {} } } },
}));
vi.mock("../useFalconAPI", () => ({
  listSkills: vi.fn(),
  fetchConnectors: vi.fn(),
}));

const skills = [{ id: "landing-skill" }];
const connectors = [{ id: "landing-connector" }];
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
async function settle() {
  await act(async () => {});
}
function mountCatalog() {
  return renderHook(({ open }) => useFalconCatalog(open), {
    initialProps: { open: true },
  });
}
function reopen(hook) {
  hook.rerender({ open: false });
  hook.rerender({ open: true });
}

beforeEach(() => {
  // Real recovery clears both catalogs and retires the preceding scope/cache.
  useFalconStore.getState().resetAll();
  h.workspace = { currentWorkspaceId: "landing", isReady: true };
  client.defaults.headers.common = {
    "X-Workspace-Id": "landing",
    "X-Organization-Id": "org",
  };
  listSkills.mockReset().mockResolvedValue({ results: skills });
  fetchConnectors.mockReset().mockResolvedValue({ results: connectors });
});
afterEach(cleanup);

describe("catalog retry after a transient endpoint failure", () => {
  describe.each(["sidebar reopen", "page remount"])("%s", (lifecycle) => {
    it.each(["skills", "connectors", "both"])(
      "retries a settled %s failure and caches the recovered catalog",
      async (failed) => {
        if (failed !== "connectors")
          listSkills.mockRejectedValueOnce(new Error("offline skills"));
        if (failed !== "skills")
          fetchConnectors.mockRejectedValueOnce(
            new Error("offline connectors"),
          );
        let hook = mountCatalog();
        await settle();
        expect(listSkills).toHaveBeenCalledTimes(1);
        expect(fetchConnectors).toHaveBeenCalledTimes(1);
        expect(useFalconStore.getState().skills).toEqual(
          failed === "connectors" ? skills : [],
        );
        expect(useFalconStore.getState().connectors).toEqual(
          failed === "skills" ? connectors : [],
        );

        if (lifecycle === "sidebar reopen") reopen(hook);
        else {
          hook.unmount();
          hook = mountCatalog();
        }
        await settle();
        // Failed endpoints must be retried; retrying the entire pair is allowed.
        if (failed !== "connectors")
          expect(listSkills).toHaveBeenCalledTimes(2);
        if (failed !== "skills")
          expect(fetchConnectors).toHaveBeenCalledTimes(2);
        expect(useFalconStore.getState().skills).toEqual(skills);
        expect(useFalconStore.getState().connectors).toEqual(connectors);
        const counts = [
          listSkills.mock.calls.length,
          fetchConnectors.mock.calls.length,
        ];
        reopen(hook);
        renderHook(() => useFalconCatalog());
        await settle();
        expect([
          listSkills.mock.calls.length,
          fetchConnectors.mock.calls.length,
        ]).toEqual(counts);
      },
    );
  });

  it("deduplicates successful loads across StrictMode, reopen and page remount", async () => {
    const hook = renderHook(({ open }) => useFalconCatalog(open), {
      initialProps: { open: true },
      wrapper: StrictMode,
    });
    await settle();
    reopen(hook);
    hook.unmount();
    mountCatalog();
    await settle();
    expect(listSkills).toHaveBeenCalledTimes(1);
    expect(fetchConnectors).toHaveBeenCalledTimes(1);
    expect(useFalconStore.getState().skills).toEqual(skills);
    expect(useFalconStore.getState().connectors).toEqual(connectors);
  });

  it("deduplicates in-flight skills and connectors across consumers and reopen", async () => {
    const pendingSkills = deferred();
    const pendingConnectors = deferred();
    listSkills.mockReturnValueOnce(pendingSkills.promise);
    fetchConnectors.mockReturnValueOnce(pendingConnectors.promise);
    const hook = mountCatalog();
    reopen(hook);
    mountCatalog();
    expect(listSkills).toHaveBeenCalledTimes(1);
    expect(fetchConnectors).not.toHaveBeenCalled();
    await act(async () => pendingSkills.resolve({ results: skills }));
    reopen(hook);
    mountCatalog();
    expect(listSkills).toHaveBeenCalledTimes(1);
    expect(fetchConnectors).toHaveBeenCalledTimes(1);
    await act(async () => pendingConnectors.resolve({ results: connectors }));
    expect(useFalconStore.getState().connectors).toEqual(connectors);
  });

  it("keeps a partially failed load deduplicated until its other endpoint settles", async () => {
    const pending = deferred();
    listSkills.mockRejectedValueOnce(new Error("offline skills"));
    fetchConnectors.mockReturnValueOnce(pending.promise);
    const hook = mountCatalog();
    await settle();
    reopen(hook);
    mountCatalog();
    await settle();
    expect(listSkills).toHaveBeenCalledTimes(1);
    expect(fetchConnectors).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve({ results: connectors }));
    expect(useFalconStore.getState().connectors).toEqual(connectors);
  });

  it.each(["skills", "connectors"])(
    "discards a stale-workspace %s result",
    async (endpoint) => {
      const pending = deferred();
      (endpoint === "skills"
        ? listSkills
        : fetchConnectors
      ).mockReturnValueOnce(pending.promise);
      const hook = mountCatalog();
      await settle();
      act(() => {
        h.workspace = { currentWorkspaceId: "next", isReady: true };
        client.defaults.headers.common["X-Workspace-Id"] = "next";
        useFalconStore.getState().resetAll();
      });
      hook.rerender({ open: true });
      await settle();
      await act(async () =>
        pending.resolve({ results: [{ id: "stale-private" }] }),
      );
      expect(useFalconStore.getState().skills).toEqual(skills);
      expect(useFalconStore.getState().connectors).toEqual(connectors);
      expect(listSkills).toHaveBeenCalledTimes(2);
      expect(fetchConnectors).toHaveBeenCalledTimes(
        endpoint === "skills" ? 1 : 2,
      );
    },
  );

  it("does not let an older failed refresh evict a newer successful cache", async () => {
    const pending = deferred();
    fetchConnectors.mockReturnValueOnce(pending.promise);
    const hook = mountCatalog();
    await settle();
    await act(async () => hook.result.current());
    await act(async () => pending.reject(new Error("older request failed")));
    reopen(hook);
    mountCatalog();
    await settle();
    expect(listSkills).toHaveBeenCalledTimes(2);
    expect(fetchConnectors).toHaveBeenCalledTimes(2);
    expect(useFalconStore.getState().skills).toEqual(skills);
    expect(useFalconStore.getState().connectors).toEqual(connectors);
  });
});
