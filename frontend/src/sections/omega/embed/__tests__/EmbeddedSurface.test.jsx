import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useNavigationType,
} from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import EmbeddedSurface from "../EmbeddedSurface";
import { FRAME_ALLOW, FRAME_CLASS } from "../frame-attributes";
import { EMBED_STATUS, NO_KEY_GATE_TIMEOUT_MS } from "../use-embed-session";

const CHILD = "http://127.0.0.1:4191";
const HELLO = { type: "embed.hello", protocol: "omega-embed/1" };

let probe;
function Probe() {
  probe = {
    location: useLocation(),
    navigate: useNavigate(),
    navigationType: useNavigationType(),
  };
  return null;
}

const renderSurface = ({
  mode = "light",
  path = "/dashboard/studio/runs/1",
  ...props
} = {}) =>
  render(
    <ThemeProvider theme={createTheme({ palette: { mode } })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/dashboard/studio/*"
            element={
              <>
                <Probe />
                <EmbeddedSurface
                  src={`${CHILD}/child.html?embed=1`}
                  basePath="/dashboard/studio"
                  getToken={() => "tok_secret"}
                  label="local Studio"
                  {...props}
                />
              </>
            }
          />
          <Route path="*" element={<Probe />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );

const frameOf = () => screen.getByTestId("omega-embed-frame");

const spyOnFrame = (frame) => {
  const posted = [];
  vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(
    (message, targetOrigin) => posted.push({ message, targetOrigin }),
  );
  return posted;
};

const fromChild = (frame, data, origin = CHILD) =>
  act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        data,
        origin,
        source: frame.contentWindow,
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

// Every status the surface committed, in order (what the user could have seen).
const recordStatuses = () => {
  const seen = [];
  return { seen, onStatusChange: (status) => seen.push(status) };
};

// For fake timers: deliver a child message without waiting on a real timer.
const fromChildNow = (frame, data, origin = CHILD) =>
  act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        data,
        origin,
        source: frame.contentWindow,
      }),
    );
    await Promise.resolve();
  });

const gateState = () => screen.queryByTestId("omega-embed-gate")?.dataset.state;

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("EmbeddedSurface", () => {
  it("frames the child without a sandbox, with the fixed attributes and no token in src", () => {
    renderSurface();
    const frame = frameOf();

    expect(frame.tagName).toBe("IFRAME");
    expect(frame.hasAttribute("sandbox")).toBe(false);
    expect(frame.getAttribute("allow")).toBe(FRAME_ALLOW);
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(frame.className).toContain("ph-no-capture");
    expect(frame.className).toContain("rr-block");
    expect(FRAME_CLASS).toBe("ph-no-capture rr-block");
    expect(frame.getAttribute("src")).toBe(
      `${CHILD}/child.html?embed=1#/runs/1`,
    );
    expect(frame.getAttribute("src")).not.toContain("tok_secret");
    expect(screen.getByTestId("omega-embed-gate").dataset.state).toBe(
      EMBED_STATUS.connecting,
    );
  });

  it("hands the key over after hello, then the theme, and hides the gate", async () => {
    renderSurface({ mode: "dark" });
    const frame = frameOf();
    const posted = spyOnFrame(frame);

    await fromChild(frame, HELLO);

    expect(posted).toEqual([
      {
        message: { type: "embed.key", token: "tok_secret" },
        targetOrigin: CHILD,
      },
      { message: { type: "embed.theme", mode: "dark" }, targetOrigin: CHILD },
    ]);
    expect(screen.getByTestId("omega-embedded-surface").dataset.state).toBe(
      EMBED_STATUS.connected,
    );
    expect(screen.queryByTestId("omega-embed-gate")).toBeNull();
  });

  it("does not answer a hello from a different origin", async () => {
    const getToken = vi.fn(() => "tok_secret");
    renderSurface({ getToken });
    const frame = frameOf();
    const posted = spyOnFrame(frame);

    await fromChild(frame, HELLO, "http://127.0.0.1:4192");

    expect(getToken).not.toHaveBeenCalled();
    expect(posted).toEqual([]);
  });

  it("mirrors child routes into the parent URL and parent navigation into the child", async () => {
    renderSurface();
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await fromChild(frame, HELLO);

    await fromChild(frame, { type: "embed.route", path: "/runs/1" });
    expect(probe.location.pathname).toBe("/dashboard/studio/runs/1");

    await fromChild(frame, { type: "embed.route", path: "/runs/2?tab=trace" });
    expect(`${probe.location.pathname}${probe.location.search}`).toBe(
      "/dashboard/studio/runs/2?tab=trace",
    );
    // A route that came from the child is not echoed back to it.
    expect(posted.filter((p) => p.message.type === "embed.navigate")).toEqual(
      [],
    );

    act(() => probe.navigate("/dashboard/studio/settings"));
    await waitFor(() =>
      expect(posted.filter((p) => p.message.type === "embed.navigate")).toEqual(
        [
          {
            message: { type: "embed.navigate", path: "/settings" },
            targetOrigin: CHILD,
          },
        ],
      ),
    );
  });

  it("opens same-origin links in the app and ignores others", async () => {
    renderSurface();
    const frame = frameOf();
    spyOnFrame(frame);
    await fromChild(frame, HELLO);

    await fromChild(frame, {
      type: "embed.open",
      href: "https://evil.example/x",
    });
    expect(probe.location.pathname).toBe("/dashboard/studio/runs/1");

    await fromChild(frame, {
      type: "embed.open",
      href: "/dashboard/observe/p1?x=1",
    });
    expect(`${probe.location.pathname}${probe.location.search}`).toBe(
      "/dashboard/observe/p1?x=1",
    );
  });

  it("shows calm gate states the child reports", async () => {
    renderSurface();
    const frame = frameOf();
    spyOnFrame(frame);
    await fromChild(frame, HELLO);

    await fromChild(frame, { type: "embed.gate", state: "needs-key" });
    expect(screen.getByTestId("omega-embed-gate").dataset.state).toBe(
      EMBED_STATUS.needsKey,
    );
    expect(
      screen.getByText("Local Studio needs to be reconnected"),
    ).toBeTruthy();

    await fromChild(frame, { type: "embed.gate", state: "refused" });
    expect(
      screen.getByText("Local Studio refused the connection"),
    ).toBeTruthy();

    await fromChild(frame, { type: "embed.gate", state: "ready" });
    expect(screen.queryByTestId("omega-embed-gate")).toBeNull();
  });

  it("waits for needs-key on a return visit without replaying a spent token", async () => {
    // The remounted child has no key: RC1 keeps keys only in memory.
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ getToken: () => null, onStatusChange, requireGate: true });
    const frame = frameOf();
    const posted = spyOnFrame(frame);

    await fromChild(frame, HELLO);
    await fromChild(frame, HELLO); // Studio says hello again every 250 ms
    expect(posted.some((p) => p.message.type === "embed.key")).toBe(false);
    expect(gateState()).toBe(EMBED_STATUS.connecting);

    await fromChild(frame, { type: "embed.gate", state: "needs-key" });
    expect(gateState()).toBe(EMBED_STATUS.needsKey);
    expect(seen).toEqual([EMBED_STATUS.connecting, EMBED_STATUS.needsKey]);
  });

  it("asks for a fresh link when the child reports it has no key", async () => {
    renderSurface({ getToken: () => null });
    const frame = frameOf();
    spyOnFrame(frame);

    await fromChild(frame, HELLO);
    await fromChild(frame, { type: "embed.gate", state: "needs-key" });

    expect(gateState()).toBe(EMBED_STATUS.needsKey);
    expect(
      screen.getByText("Local Studio needs to be reconnected"),
    ).toBeTruthy();
  });

  it("falls back to needs-key once, if a child we hold no key for never reports a gate", async () => {
    vi.useFakeTimers();
    renderSurface({ getToken: () => null });
    const frame = frameOf();
    spyOnFrame(frame);

    await fromChildNow(frame, HELLO);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NO_KEY_GATE_TIMEOUT_MS - 3000);
    });
    // A later hello does not push the bound out.
    await fromChildNow(frame, HELLO);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2900);
    });
    expect(gateState()).toBe(EMBED_STATUS.connecting);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(gateState()).toBe(EMBED_STATUS.needsKey);
    expect(NO_KEY_GATE_TIMEOUT_MS).toBeGreaterThan(3000); // Studio waits 3 s
  });

  it("keeps a working frame when the child says hello again after the key", async () => {
    let token = "tok_once";
    const getToken = () => {
      const value = token;
      token = null;
      return value;
    };
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ getToken, onStatusChange, noKeyTimeoutMs: 20 });
    const frame = frameOf();
    const posted = spyOnFrame(frame);

    await fromChild(frame, HELLO);
    await fromChild(frame, HELLO); // sent before the key reached the child
    await act(() => new Promise((resolve) => setTimeout(resolve, 60)));

    expect(posted.filter((p) => p.message.type === "embed.key")).toHaveLength(
      1,
    );
    expect(gateState()).toBeUndefined();
    expect(seen).toEqual([EMBED_STATUS.connecting, EMBED_STATUS.connected]);
  });

  it("goes back to connecting when a late hello we hold no key for arrives after the timeout", async () => {
    renderSurface({ getToken: () => null, helloTimeoutMs: 20 });
    const frame = frameOf();
    spyOnFrame(frame);
    await waitFor(() => expect(gateState()).toBe(EMBED_STATUS.timeout));

    await fromChild(frame, HELLO);
    expect(gateState()).toBe(EMBED_STATUS.connecting);
    expect(
      screen.getByTestId("omega-embed-gate").dataset.cause,
    ).toBeUndefined();

    await fromChild(frame, { type: "embed.gate", state: "needs-key" });
    expect(gateState()).toBe(EMBED_STATUS.needsKey);
  });

  it("replaces the history entry for the child's first route after a hello, then pushes", async () => {
    renderSurface();
    const frame = frameOf();
    spyOnFrame(frame);
    await fromChild(frame, HELLO);

    await fromChild(frame, { type: "embed.route", path: "/runs/3" });
    expect(probe.location.pathname).toBe("/dashboard/studio/runs/3");
    expect(probe.navigationType).toBe("REPLACE");

    await fromChild(frame, { type: "embed.route", path: "/runs/4" });
    expect(probe.location.pathname).toBe("/dashboard/studio/runs/4");
    expect(probe.navigationType).toBe("PUSH");

    // A new hello (the frame reloaded) starts over with a replace.
    await fromChild(frame, HELLO);
    await fromChild(frame, { type: "embed.route", path: "/runs/5" });
    expect(probe.location.pathname).toBe("/dashboard/studio/runs/5");
    expect(probe.navigationType).toBe("REPLACE");

    act(() => probe.navigate(-1));
    expect(probe.location.pathname).toBe("/dashboard/studio/runs/3");
  });

  it("explains the problem when no hello arrives within the timeout", async () => {
    vi.useFakeTimers();
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderSurface({ helloTimeoutMs: 6000 });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5900);
    });
    expect(screen.getByTestId("omega-embed-gate").dataset.state).toBe(
      EMBED_STATUS.connecting,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    vi.useRealTimers();
    await waitFor(() =>
      expect(screen.getByTestId("omega-embed-gate").dataset.state).toBe(
        EMBED_STATUS.timeout,
      ),
    );
    expect(screen.getByTestId("omega-embed-gate").dataset.cause).toBe(
      "no-response",
    );

    act(() => screen.getByText("Open local Studio directly").click());
    expect(open).toHaveBeenCalledWith(
      `${CHILD}/child.html`,
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("recovers when a late hello arrives after the timeout", async () => {
    renderSurface({ helloTimeoutMs: 20 });
    const frame = frameOf();
    spyOnFrame(frame);
    await waitFor(() =>
      expect(screen.getByTestId("omega-embed-gate").dataset.state).toBe(
        EMBED_STATUS.timeout,
      ),
    );

    await fromChild(frame, HELLO);
    expect(screen.queryByTestId("omega-embed-gate")).toBeNull();
  });
});
