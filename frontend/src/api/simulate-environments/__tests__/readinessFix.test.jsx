import { describe, it, expect, vi, beforeEach } from "vitest";
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
