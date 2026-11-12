import { afterEach, describe, expect, it } from "vitest";
import { useLLMTracingStore } from "../states";

// The URL-synced setters rewrite the current entry's query in place. They must
// keep the router's history.state ({ usr, key, idx }), or a later Back/Forward
// onto that entry can no longer be told apart from its neighbors.
describe("LLMTracing URL-synced state keeps the router's history state", () => {
  afterEach(() => {
    useLLMTracingStore.getState().resetStates();
    window.history.replaceState(null, "", "/");
  });

  it.each([
    ["traceDetailDrawerOpen", "setTraceDetailDrawerOpen"],
    ["spanDetailDrawerOpen", "setSpanDetailDrawerOpen"],
  ])("%s opens and closes without erasing the entry", (key, setterName) => {
    const routerState = { usr: { from: "users" }, key: "entry-4", idx: 4 };
    window.history.replaceState(
      routerState,
      "",
      "/dashboard/users/bob?userTab=traces",
    );
    const setter = useLLMTracingStore.getState()[setterName];
    const value = { traceId: "t1", projectId: "p1" };

    setter(value);
    expect(new URLSearchParams(window.location.search).get(key)).toBe(
      JSON.stringify(value),
    );
    expect(new URLSearchParams(window.location.search).get("userTab")).toBe(
      "traces",
    );
    expect(window.history.state).toEqual(routerState);

    setter(null);
    expect(window.location.search).toBe("?userTab=traces");
    expect(window.history.state).toEqual(routerState);
    expect(useLLMTracingStore.getState()[key]).toBeNull();
  });
});
