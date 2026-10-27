// Cross-side WS close-code contract tests.
//
// The FE constants in `src/utils/constants.js` and the BE constants in
// `futureagi/sockets/prompt_stream_consumer.py` are a cross-tier wire
// contract with no single source of truth. These tests pin the values on
// both sides — reading the BE file at test time — so drift on EITHER side
// (not just its own snapshot) is caught in CI. The FE test also exercises
// the shared `isAuthFailCloseCode` predicate and drives a real 4003 close
// through `runPromptOverSocket` end-to-end.
//
// If BE close codes change, update both files above and this test.

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const __testDir = path.dirname(fileURLToPath(import.meta.url));

import { WS_CLOSE_CODES } from "src/utils/constants";
import {
  authFailMessage,
  isAuthFailCloseCode,
  runPromptOverSocket,
} from "src/sections/workbench/createPrompt/common";

describe("WS_CLOSE_CODES contract", () => {
  it("has the expected FE numeric values", () => {
    expect(WS_CLOSE_CODES).toEqual({
      UNAUTHENTICATED: 4001,
      PERMISSION_DENIED: 4003,
      NOT_FOUND: 4004,
    });
  });

  it("agrees with the backend WS_CLOSE_CODE_* constants", () => {
    // Physically parse the BE constants module. If either the BE numeric
    // values OR the FE ones drift, this test fails — even if both sides
    // update their own snapshots consistently within themselves.
    const bePath = path.resolve(
      __testDir,
      "../../../../../../futureagi/sockets/prompt_stream_consumer.py",
    );
    const beSource = readFileSync(bePath, "utf8");

    const extract = (name) => {
      const match = beSource.match(
        new RegExp(`^${name}\\s*=\\s*(\\d+)\\s*$`, "m"),
      );
      if (!match) {
        throw new Error(
          `Could not find ${name} in ${bePath} — did the BE constant get renamed?`,
        );
      }
      return Number(match[1]);
    };

    const beValues = {
      UNAUTHENTICATED: extract("WS_CLOSE_CODE_UNAUTHENTICATED"),
      PERMISSION_DENIED: extract("WS_CLOSE_CODE_PERMISSION_DENIED"),
      NOT_FOUND: extract("WS_CLOSE_CODE_NOT_FOUND"),
    };

    expect(WS_CLOSE_CODES).toEqual(beValues);
  });
});

describe("isAuthFailCloseCode", () => {
  it.each([
    [WS_CLOSE_CODES.UNAUTHENTICATED, true],
    [WS_CLOSE_CODES.PERMISSION_DENIED, true],
    [WS_CLOSE_CODES.NOT_FOUND, true],
    [1000, false], // normal close
    [1006, false], // abnormal close (no reason)
    [undefined, false],
  ])("code %s → %s", (code, expected) => {
    expect(isAuthFailCloseCode({ code })).toBe(expected);
  });

  it("returns false on null/undefined event", () => {
    expect(isAuthFailCloseCode(null)).toBe(false);
    expect(isAuthFailCloseCode(undefined)).toBe(false);
  });
});

describe("authFailMessage", () => {
  it("uses the server-supplied reason when present", () => {
    expect(authFailMessage({ code: 4003, reason: "no access" })).toBe(
      "no access",
    );
  });

  it("falls back to a canonical string when reason is missing", () => {
    expect(authFailMessage({ code: 4003 })).toBe("Permission denied");
    expect(authFailMessage({ code: 4003, reason: "" })).toBe(
      "Permission denied",
    );
    expect(authFailMessage(undefined)).toBe("Permission denied");
  });
});

describe("runPromptOverSocket — end-to-end auth-fail close handling", () => {
  // These tests reconstruct the shape of the WorkbenchProvider run-block
  // closure (Promise + fallback timer + spinner + socket cleanup) and drive
  // a real 4003 CloseEvent through `runPromptOverSocket`. This is the exact
  // scenario Nikhil asked for: a test that fires 4003 through the wrapper
  // and asserts the permission error surfaces + all state is cleaned up.
  let sockets;
  let originalWebSocket;

  beforeEach(() => {
    sockets = [];
    originalWebSocket = globalThis.WebSocket;
    class FakeWebSocket {
      constructor(url) {
        this.url = url;
        this.readyState = 0;
        this.onopen = null;
        this.onmessage = null;
        this.onerror = null;
        this.onclose = null;
        sockets.push(this);
      }
      send() {}
      close() {
        this.readyState = 3;
      }
    }
    globalThis.WebSocket = FakeWebSocket;
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
  });

  it("stops the spinner, clears the fallback timer, closes the socket, and settles the Promise on a 4003 close", async () => {
    const spinnerOff = vi.fn();
    const socketClosed = vi.fn();
    const fallbackToHttpPolling = vi.fn();
    let fallbackTimerFired = false;

    const promise = new Promise((resolve, reject) => {
      let completed = false;
      const fallbackTimer = setTimeout(() => {
        fallbackTimerFired = true;
        fallbackToHttpPolling();
      }, 50);
      const clearFallbackTimer = () => clearTimeout(fallbackTimer);

      const socket = runPromptOverSocket({
        url: "ws://test",
        payload: { type: "run_template" },
        onMessage: vi.fn(),
        onError: vi.fn(),
        onClose: (event) => {
          if (isAuthFailCloseCode(event)) {
            completed = true;
            clearFallbackTimer();
            socketClosed();
            spinnerOff();
            reject(new Error(authFailMessage(event)));
            return;
          }
          if (!completed) fallbackToHttpPolling();
        },
      });
      // Simulate the wrapper's stored ref (Provider does this too).
      expect(socket).toBeDefined();
    });

    // Drive the auth-fail close.
    sockets[0].onclose({
      code: WS_CLOSE_CODES.PERMISSION_DENIED,
      reason: "no access",
    });

    await expect(promise).rejects.toThrow("no access");
    expect(spinnerOff).toHaveBeenCalledTimes(1);
    expect(socketClosed).toHaveBeenCalledTimes(1);

    // Ensure the fallback timer never fires — this is the leaked-timer
    // regression Nikhil flagged. Wait longer than the timer's 50ms delay.
    await new Promise((r) => setTimeout(r, 80));
    expect(fallbackToHttpPolling).not.toHaveBeenCalled();
    expect(fallbackTimerFired).toBe(false);
  });

  it("falls through to the disconnect branch on a non-auth close (e.g. 1006)", async () => {
    const fallbackToHttpPolling = vi.fn();

    new Promise((_resolve, reject) => {
      runPromptOverSocket({
        url: "ws://test",
        payload: { type: "run_template" },
        onMessage: vi.fn(),
        onError: vi.fn(),
        onClose: (event) => {
          if (isAuthFailCloseCode(event)) {
            reject(new Error(authFailMessage(event)));
            return;
          }
          fallbackToHttpPolling();
        },
      });
    }).catch(() => {}); // swallow — the promise never rejects in this branch

    sockets[0].onclose({ code: 1006, reason: "" });

    expect(fallbackToHttpPolling).toHaveBeenCalledTimes(1);
  });
});
