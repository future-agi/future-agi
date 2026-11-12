import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import PropTypes from "prop-types";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

const { default: axios } = await import("src/utils/axios");
const { startGroupFix, useFollowFixRun } = await import("../readinessFix");

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
wrapper.propTypes = { children: PropTypes.node };

beforeEach(() => {
  client = new QueryClient();
  axios.post.mockReset();
});

const run = (extra = {}) => ({
  state: "fixing",
  updated_at: "t1",
  saved: null,
  draft: false,
  ...extra,
});

describe("startGroupFix", () => {
  it("posts the group with only the picked pieces", async () => {
    axios.post.mockResolvedValue({ data: { fix: { id: "f1" } } });
    await startGroupFix("e1", {
      group: "builder",
      picks: { environment: "ev2" },
      idempotencyKey: "k1",
    });
    expect(axios.post).toHaveBeenCalledWith(
      "/simulate/api/harness-environments/e1/capabilities/fix/",
      {
        group: "builder",
        idempotency_key: "k1",
        environment_version_id: "ev2",
      },
    );
  });
});

describe("useFollowFixRun", () => {
  it("does nothing on the first run, then refreshes versions and drafts once it is saved", () => {
    const spy = vi.spyOn(client, "invalidateQueries");
    const { rerender } = renderHook(
      ({ fixRun }) => useFollowFixRun("e1", fixRun),
      { wrapper, initialProps: { fixRun: run() } },
    );
    expect(spy).not.toHaveBeenCalled();

    rerender({
      fixRun: run({
        state: "done",
        updated_at: "t2",
        saved: { environment: { id: "ev3" }, suite: null },
      }),
    });
    const keys = spy.mock.calls.map(([arg]) => arg.queryKey[0]);
    expect(keys).toContain("harness-readiness");
    expect(keys).toContain("harness-env-versions");
  });
});

describe("dummy", () => {
  const clear = () =>
    Object.keys(localStorage)
      .filter((key) => key.startsWith("fi.readinessFixDummy"))
      .forEach((key) => localStorage.removeItem(key));

  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetModules();
    clear();
  });
  afterEach(() => {
    vi.useRealTimers();
    clear();
  });

  const load = () => import("../readinessFix.dummy");
  const report = () => ({
    pairing: {},
    rows: [
      {
        id: "start",
        status: "blocked",
        fix: { type: "preflight", action: "a" },
      },
      { id: "reset", status: "supported", fix: null },
    ],
    areas: [{ id: "reset", rows: ["reset"] }],
  });

  it("is off unless the flag is 1", async () => {
    const dummy = await load();
    expect(dummy.dummyOn()).toBe(false);
    localStorage.setItem("fi.readinessFixDummy", "1");
    expect(dummy.dummyOn()).toBe(true);
  });

  it("groups rows and adds the Fix all message and a builder row", async () => {
    const dummy = await load();
    const out = dummy.augmentReport("e1", {}, report());
    expect(out.rows.map((row) => row.fix_group)).toEqual([
      "needs_you",
      null,
      "builder",
      "builder",
      "recheck",
    ]);
    expect(out.fix_groups.builder.rows).toEqual([
      "dummy_tool_routing",
      "dummy_seed_data",
    ]);
    expect(out.fix_groups.builder.message).toContain("lookup_order");
    expect(out.areas.find((a) => a.id === "tool_binding").rows).toEqual([
      "dummy_tool_routing",
    ]);
  });

  it("steps a started run through its states, and survives a module reset", async () => {
    localStorage.setItem("fi.readinessFixDummy", "1");
    let dummy = await load();
    dummy.augmentReport("e1", {}, report());
    await dummy.dummyStartFix("e1", { picks: {}, newest: { id: "ev3" } });
    const states = [];
    for (const ms of [0, 8000, 12000, 10000, 8000]) {
      vi.advanceTimersByTime(ms);
      states.push(dummy.dummyFixRun("e1").state);
    }
    expect(states).toEqual([
      "fixing",
      "waiting_on_you",
      "fixing",
      "checking",
      "done",
    ]);

    vi.resetModules();
    dummy = await load();
    const done = dummy.dummyFixRun("e1");
    expect(done.saved.environment.id).toBe("ev3");
    const out = dummy.augmentReport("e1", {}, report());
    const row = out.rows.find((r) => r.id === "dummy_tool_routing");
    expect(row.status).toBe("supported");
    expect(row.fix_progress.state).toBe("fixed");
  });
});
