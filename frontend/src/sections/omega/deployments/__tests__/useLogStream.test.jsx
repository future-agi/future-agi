import React from "react";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DeploymentsApiProvider } from "../api/DeploymentsApiContext";
import { LOG_STATE, useLogStream } from "../lib/useLogStream";
import { ID } from "./helpers";

/**
 * A scripted log endpoint: each connect replays the next script entry. An
 * entry is { events, end?, fail? }: `end` sends the log's own `event: end`,
 * otherwise the transport just closes (what EE does when a host assertion
 * expires mid-stream).
 */
function scriptedLogs(script) {
  const calls = [];
  const logs = vi.fn((id, options) => {
    const step = script[Math.min(calls.length, script.length - 1)];
    calls.push(options);
    let closed = false;
    const done = (async () => {
      await Promise.resolve();
      for (const event of step.events || []) options.onEvent(event);
      if (step.end)
        options.onEvent({ event: "end", json: { reason: step.end } });
      if (step.fail)
        return { ok: false, state: "refused", code: "x", message: step.fail };
      if (step.failWith) return { ok: false, ...step.failWith };
      if (step.hang) await new Promise(() => {});
      return closed ? { closed: true } : { ended: true };
    })();
    return {
      close: () => {
        closed = true;
      },
      done,
    };
  });
  return { logs, calls };
}

const line = (seq, text) => ({
  event: "log",
  json: { seq, at: "2026-10-14T00:00:00Z", line: text, source: "build" },
});
const runtime = (text) => ({
  event: "log",
  json: {
    at: "2026-10-14T00:00:00Z",
    line: text,
    source: "runtime",
    revision: 1,
  },
});

function wrapperFor(api) {
  function Wrapper({ children }) {
    return (
      <DeploymentsApiProvider api={api}>{children}</DeploymentsApiProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
}

describe("useLogStream", () => {
  it("reopens a cut build log after its last seq and never repeats a line", async () => {
    const { logs, calls } = scriptedLogs([
      { events: [line(1, "Step 1/6"), line(2, "Step 2/6")] },
      {
        events: [line(2, "Step 2/6"), line(3, "Revision 1 is live.")],
        end: "revision log closed",
      },
    ]);
    const { result } = renderHook(
      () => useLogStream(ID, { source: "build", revision: 1 }),
      {
        wrapper: wrapperFor({ logs }),
      },
    );
    await waitFor(() => expect(result.current.state).toBe(LOG_STATE.ENDED), {
      timeout: 3000,
    });
    expect(result.current.lines.map((l) => l.line)).toEqual([
      "Step 1/6",
      "Step 2/6",
      "Revision 1 is live.",
    ]);
    expect(calls[0].lastEventId).toBeNull();
    expect(calls[1].lastEventId).toBe("2");
    expect(result.current.endReason).toBe("revision log closed");
  });

  it("reopens a cut runtime log with tail 0 so the tail is not printed twice", async () => {
    const { logs, calls } = scriptedLogs([
      { events: [runtime("Omega agent server: fixture, 3 entries")] },
      { events: [runtime("POST /studio/v1/runs 200")], hang: true },
    ]);
    const { result, unmount } = renderHook(
      () => useLogStream(ID, { source: "runtime" }),
      {
        wrapper: wrapperFor({ logs }),
      },
    );
    await waitFor(() => expect(result.current.lines).toHaveLength(2), {
      timeout: 3000,
    });
    expect(calls[0].tail).toBe(200);
    expect(calls[1].tail).toBe(0);
    expect(result.current.state).toBe(LOG_STATE.STREAMING);
    unmount();
  });

  it("notes a rollback once, where the revision changes, even when the old container is re-read while draining", async () => {
    const at = (second) => `2026-10-14T00:00:0${second}Z`;
    const r2 = (second, text) => ({
      event: "log",
      json: { at: at(second), line: text, source: "runtime", revision: 2 },
    });
    const r1 = (second, text) => ({
      event: "log",
      json: { at: at(second), line: text, source: "runtime", revision: 1 },
    });
    const { logs, calls } = scriptedLogs([
      // Following revision 2; its stream ends while it is still draining.
      {
        events: [r2(0, "Omega agent server: fixture")],
        end: "container stopped",
      },
      // Re-attached to the draining container: one old line, one new line.
      {
        events: [
          r2(0, "Omega agent server: fixture"),
          r2(1, "omega serve: stopped"),
        ],
        end: "container stopped",
      },
      // Read once more before EE swaps the active container: nothing new.
      {
        events: [
          r2(0, "Omega agent server: fixture"),
          r2(1, "omega serve: stopped"),
        ],
        end: "container stopped",
      },
      // Revision 1 is serving now.
      {
        events: [r1(5, "Omega agent server: fixture")],
        end: "nothing is running",
      },
    ]);
    const { result } = renderHook(
      () => useLogStream(ID, { source: "runtime" }),
      { wrapper: wrapperFor({ logs }) },
    );
    await waitFor(() => expect(result.current.state).toBe(LOG_STATE.ENDED), {
      timeout: 8000,
    });
    expect(result.current.lines.map((l) => l.line)).toEqual([
      "Omega agent server: fixture",
      "omega serve: stopped",
      "Revision 2 stopped. Following revision 1.",
      "Omega agent server: fixture",
    ]);
    // Each next container is read from its first line, not as a tail-0 continuation.
    expect(calls.map((c) => c.tail)).toEqual([200, 200, 200, 200]);
    expect(result.current.endReason).toBe("nothing is running");
  });

  it("stops on a refusal and on a stream that keeps closing", async () => {
    const refused = scriptedLogs([{ fail: "Deployments aren't reachable" }]);
    const first = renderHook(
      () => useLogStream(ID, { source: "build", revision: 1 }),
      {
        wrapper: wrapperFor({ logs: refused.logs }),
      },
    );
    await waitFor(() =>
      expect(first.result.current.state).toBe(LOG_STATE.ERROR),
    );
    expect(refused.calls).toHaveLength(1);

    const flapping = scriptedLogs([{ events: [] }]);
    const second = renderHook(
      () => useLogStream(ID, { source: "build", revision: 1 }),
      {
        wrapper: wrapperFor({ logs: flapping.logs }),
      },
    );
    await waitFor(
      () => expect(second.result.current.state).toBe(LOG_STATE.ERROR),
      { timeout: 5000 },
    );
    expect(flapping.calls).toHaveLength(5);
    act(() => second.result.current.restart());
    await waitFor(() => expect(flapping.calls.length).toBeGreaterThan(5));
    second.unmount();
  });
  it("opens again after a momentary 401 and keeps a runtime log's tail", async () => {
    const refused = {
      state: "refused",
      status: 401,
      code: "unauthorized",
      message: "Valid Omega API key required.",
    };
    const { logs, calls } = scriptedLogs([
      { failWith: refused },
      { failWith: refused },
      { events: [runtime("Omega agent server listening")], end: "ended" },
    ]);
    const { result } = renderHook(
      () => useLogStream(ID, { source: "runtime" }),
      { wrapper: wrapperFor({ logs }) },
    );
    await waitFor(() => expect(result.current.state).toBe(LOG_STATE.ENDED), {
      timeout: 4000,
    });
    expect(result.current.error).toBeNull();
    expect(result.current.lines.map((l) => l.line)).toEqual([
      "Omega agent server listening",
    ]);
    expect(calls.map((c) => c.tail)).toEqual([200, 200, 200]);
  });

  it("shows a refusal that is not transient at once, and gives up on a lasting 401", async () => {
    const forbidden = scriptedLogs([
      { failWith: { state: "refused", status: 403, code: "role_forbidden" } },
    ]);
    const first = renderHook(() => useLogStream(ID, { source: "build" }), {
      wrapper: wrapperFor({ logs: forbidden.logs }),
    });
    await waitFor(() =>
      expect(first.result.current.state).toBe(LOG_STATE.ERROR),
    );
    expect(forbidden.calls).toHaveLength(1);
    first.unmount();

    const lasting = scriptedLogs([
      { failWith: { state: "refused", status: 401, code: "unauthorized" } },
    ]);
    const second = renderHook(() => useLogStream(ID, { source: "build" }), {
      wrapper: wrapperFor({ logs: lasting.logs }),
    });
    await waitFor(
      () => expect(second.result.current.state).toBe(LOG_STATE.ERROR),
      { timeout: 6000 },
    );
    expect(lasting.calls).toHaveLength(4);
    expect(second.result.current.error.status).toBe(401);
    second.unmount();
  }, 10_000);
});
