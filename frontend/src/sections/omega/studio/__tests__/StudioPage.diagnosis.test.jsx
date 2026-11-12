// @vitest-environment-options {"url": "https://app.futureagi.com/dashboard/studio"}
//
// Why a framed Studio never said hello, as the Studio tab explains it. The page
// runs on an https origin here, as Future AGI does in production, so the
// browser-specific causes (Safari's block on http inside https, the
// local-network permission, Brave Shields) can be reached with stubbed
// browser signals. Real browsers on loopback http never raise them locally.
import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HELLO_TIMEOUT_MS } from "src/sections/omega/embed";

import { STUDIO_HELLO_TIMEOUT_MS } from "../studio-copy";
import {
  HELLO,
  SERVER,
  TOKEN,
  frame,
  fromChild,
  planLink,
  renderStudio,
  resetStudioTestState,
  spyOnFrame,
} from "./studio-test-utils";

const SAFARI_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15";
const CHROME_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36";

const stubNavigator = (props) => {
  for (const [key, value] of Object.entries(props)) {
    Object.defineProperty(window.navigator, key, {
      configurable: true,
      get: () => value,
    });
  }
};

const reachable = () => vi.fn(() => Promise.resolve(new Response(null)));
const unreachable = () =>
  vi.fn(() => Promise.reject(new TypeError("Failed to fetch")));

async function waitForDiagnosis() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(STUDIO_HELLO_TIMEOUT_MS + 50);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10);
  });
  return screen.getByTestId("omega-embed-gate");
}

beforeEach(() => {
  resetStudioTestState();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  stubNavigator({
    userAgent: CHROME_UA,
    brave: undefined,
    permissions: undefined,
  });
  planLink();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  resetStudioTestState();
});

describe("Studio diagnosis when the frame never says hello", () => {
  it("runs on an https parent", () => {
    expect(window.location.protocol).toBe("https:");
  });

  it("puts the cause on screen within 6 s of the frame mounting, sooner than the shared default", async () => {
    globalThis.fetch = reachable();
    renderStudio();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STUDIO_HELLO_TIMEOUT_MS - 100);
    });
    expect(screen.getByTestId("omega-embed-gate")).toHaveAttribute(
      "data-state",
      "connecting",
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5900 - (STUDIO_HELLO_TIMEOUT_MS - 100));
    });
    expect(5900).toBeLessThan(HELLO_TIMEOUT_MS);
    expect(screen.getByTestId("omega-embed-gate")).toHaveAttribute(
      "data-cause",
      "framing-blocked",
    );
  });

  it("still connects when Studio says hello after the cause is shown", async () => {
    globalThis.fetch = reachable();
    renderStudio();
    await waitForDiagnosis();
    const posted = spyOnFrame(frame());
    await fromChild(frame(), HELLO);
    expect(posted[0]).toEqual({
      message: { type: "embed.key", token: TOKEN },
      targetOrigin: SERVER,
    });
    expect(posted.filter((p) => p.message.type === "embed.key")).toHaveLength(
      1,
    );
    // Handing the key over proves nothing: Studio checks it first.
    expect(screen.getByTestId("studio-status")).toHaveAttribute(
      "data-state",
      "checking",
    );
    await fromChild(frame(), { type: "embed.gate", state: "ready" });
    expect(screen.queryByTestId("omega-embed-gate")).toBeNull();
    expect(screen.getByTestId("studio-status")).toHaveAttribute(
      "data-state",
      "connected",
    );
  });

  it("names a server that is up but was not started for this site, with the exact command", async () => {
    globalThis.fetch = reachable();
    renderStudio();
    const gate = await waitForDiagnosis();
    expect(gate).toHaveAttribute("data-cause", "framing-blocked");
    expect(gate).toHaveTextContent("Studio didn't open inside Future AGI");
    expect(screen.getByTestId("studio-gate-fix-text")).toHaveTextContent(
      "omega dev --no-open --embed-origin https://app.futureagi.com",
    );
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("studio-gate-fix-copy"));
    });
    expect(writeText).toHaveBeenCalledWith(
      "omega dev --no-open --embed-origin https://app.futureagi.com",
    );
    // The probe asked Studio's own server, without credentials or a key.
    const [url, init] = globalThis.fetch.mock.calls[0];
    expect(url).toBe(`${SERVER}/studio/v1/openapi.json`);
    expect(init).toMatchObject({ mode: "no-cors", credentials: "omit" });
    expect(screen.getByTestId("studio-status")).toHaveAttribute(
      "data-state",
      "timeout",
    );
  });

  it("says nothing is answering when the server is not running, and offers local Studio", async () => {
    globalThis.fetch = unreachable();
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderStudio();
    const gate = await waitForDiagnosis();
    expect(gate).toHaveAttribute("data-cause", "not-running");
    expect(gate).toHaveTextContent(`Can't reach Studio at ${SERVER}`);
    fireEvent.click(screen.getByText("Open local Studio directly"));
    expect(open).toHaveBeenCalledWith(
      `${SERVER}/`,
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("names the browser's local-network permission when it was denied", async () => {
    globalThis.fetch = reachable();
    stubNavigator({
      permissions: {
        query: vi.fn(({ name }) =>
          name === "local-network-access"
            ? Promise.resolve({ state: "denied" })
            : Promise.reject(new TypeError("unknown permission")),
        ),
      },
    });
    renderStudio();
    const gate = await waitForDiagnosis();
    expect(gate).toHaveAttribute("data-cause", "local-network-denied");
    expect(gate).toHaveTextContent("Your browser blocked the local connection");
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("names a pending local-network permission, not a stopped server, when the probe hangs", async () => {
    globalThis.fetch = vi.fn(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    stubNavigator({
      permissions: {
        query: vi.fn(({ name }) =>
          name === "local-network-access"
            ? Promise.resolve({ state: "prompt" })
            : Promise.reject(new TypeError("unknown permission")),
        ),
      },
    });
    renderStudio();
    await waitForDiagnosis();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    const gate = screen.getByTestId("omega-embed-gate");
    expect(gate).toHaveAttribute("data-cause", "local-network-prompt");
    expect(gate).toHaveTextContent(
      "Your browser is asking to reach apps on this device",
    );
    expect(gate).not.toHaveTextContent("Can't reach Studio");
  });

  it("names Safari's block on a local http page inside https Future AGI", async () => {
    globalThis.fetch = reachable();
    stubNavigator({ userAgent: SAFARI_UA });
    renderStudio();
    const gate = await waitForDiagnosis();
    expect(gate).toHaveAttribute("data-cause", "safari");
    expect(gate).toHaveTextContent("Safari may be blocking this local page");
    expect(gate).toHaveTextContent(
      "Local Studio connects inside Future AGI in Chrome and Firefox.",
    );
    expect(screen.getByText("Open local Studio directly")).toBeVisible();
  });

  it("names Brave Shields when Brave cannot reach the server", async () => {
    globalThis.fetch = unreachable();
    stubNavigator({ brave: { isBrave: () => Promise.resolve(true) } });
    renderStudio();
    const gate = await waitForDiagnosis();
    expect(gate).toHaveAttribute("data-cause", "brave");
    expect(gate).toHaveTextContent("Brave Shields may be blocking");
  });

  it("starts a fresh attempt on Try again", async () => {
    globalThis.fetch = unreachable();
    renderStudio();
    await waitForDiagnosis();
    fireEvent.click(screen.getByText("Try again"));
    expect(screen.getByTestId("omega-embed-gate")).toHaveAttribute(
      "data-state",
      "connecting",
    );
  });
});
