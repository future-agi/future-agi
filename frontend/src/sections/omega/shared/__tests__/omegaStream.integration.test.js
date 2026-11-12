import { afterEach, describe, expect, it } from "vitest";

import { createOmegaClient } from "../omegaClient";
import { openOmegaStream } from "../omegaStream";
import { OMEGA_STATE } from "../omegaStates";
import { resetStreamMode } from "../streamMode";
import {
  freshKeys,
  startBufferingProxy,
  startSseServer,
} from "./sseServer.fixture";

/**
 * The shared client over real sockets (Node's fetch, a real HTTP server, a
 * real buffering proxy). The browser runs of the same code are in
 * __tests__/browser/ (Chromium, WebKit, Firefox).
 */
const open = [];
afterEach(async () => {
  resetStreamMode();
  while (open.length) await open.pop().close();
});

const clientFor = (base) =>
  createOmegaClient({
    basePath: base,
    fetchImpl: (...args) => fetch(...args),
    mintAssertion: freshKeys(),
  });

describe("openOmegaStream over real sockets (integration)", () => {
  it("reads a long stream across many cuts, clean and dropped, whole, in order, once, a fresh key per connection (L06)", async () => {
    const server = await startSseServer({ total: 60, everyMs: 15, cutMs: 120 });
    open.push(server);
    const { omegaFetch } = clientFor(server.base);
    const ids = [];
    const result = await openOmegaStream("/workspace/feed", {
      omegaFetch,
      terminalEvents: ["end"],
      retryDelayMs: 20,
      onEvent: (event) => {
        if (event.event === "log") ids.push(event.json.n);
      },
    }).done;
    expect(result).toEqual({ ended: true });
    expect(ids).toEqual(Array.from({ length: 60 }, (_, i) => i + 1));
    const cuts = server.log.connections.map((c) => c.cut).filter(Boolean);
    expect(cuts).toContain("ended");
    expect(cuts).toContain("destroyed");
    expect(server.log.connections.length).toBeGreaterThanOrEqual(5);
    expect(new Set(server.log.keys).size).toBe(server.log.keys.length);
    expect(server.log.reused).toBe(0);
    // Each reconnect resumed exactly after the last event it saw.
    for (const [index, connection] of server.log.connections.entries()) {
      if (index > 0) expect(connection.after).toBeGreaterThan(0);
    }
  });

  it("without terminal events the first clean cut still ends the stream (legacy callers unchanged)", async () => {
    const server = await startSseServer({
      total: 60,
      everyMs: 15,
      cutMs: 120,
      cutStyle: "end",
    });
    open.push(server);
    const { omegaFetch } = clientFor(server.base);
    const result = await openOmegaStream("/workspace/feed", { omegaFetch })
      .done;
    expect(result).toEqual({ ended: true });
    expect(server.log.connections).toHaveLength(1);
  });

  it("behind a buffering proxy, detects it within 10 s on the default timer and polls with no lost event (CH-12)", async () => {
    const server = await startSseServer({
      total: 40,
      everyMs: 300,
      cutMs: 60_000,
    });
    open.push(server);
    const proxy = await startBufferingProxy(server.origin);
    open.push(proxy);
    const { omegaFetch } = clientFor(`${proxy.origin}/omega/api`);
    const ids = [];
    const started = Date.now();
    const result = await openOmegaStream("/workspace/feed", {
      omegaFetch,
      transport: "auto",
      terminalEvents: ["end"],
      onEvent: (event) => {
        if (event.event === "log") ids.push(event.json.n);
      },
    }).done;
    expect(result).toEqual({ ended: true, mode: "poll" });
    expect(ids).toEqual(Array.from({ length: 40 }, (_, i) => i + 1));
    const firstPoll = server.log.connections.find((c) => c.mode === "poll");
    // The server clock started with the stream; the first window opened inside the bar.
    expect(firstPoll.at).toBeLessThan(10_000);
    expect(server.log.connections[0].mode).toBe("stream");
    expect(
      server.log.connections.slice(1).every((c) => c.mode === "poll"),
    ).toBe(true);
    expect(Date.now() - started).toBeLessThan(25_000);
  }, 40_000);

  it("without a proxy, auto stays on streaming", async () => {
    const server = await startSseServer({ total: 20, everyMs: 10, cutMs: 100 });
    open.push(server);
    const { omegaFetch } = clientFor(server.base);
    const result = await openOmegaStream("/workspace/feed", {
      omegaFetch,
      transport: "auto",
      terminalEvents: ["end"],
    }).done;
    expect(result).toEqual({ ended: true, mode: "stream" });
    expect(server.log.connections.every((c) => c.mode === "stream")).toBe(true);
  });
});

describe("omegaFetch over real sockets (integration)", () => {
  it("shows Future AGI copy for every EE refusal and never EE's text (L07)", async () => {
    const server = await startSseServer();
    open.push(server);
    const { omegaFetch } = clientFor(server.base);
    for (const [status, code, state] of [
      [401, "assertion_invalid", OMEGA_STATE.REFUSED],
      [403, "identity_not_connected", OMEGA_STATE.REFUSED],
      [401, "unauthorized", OMEGA_STATE.REFUSED],
      [503, "verification_unavailable", OMEGA_STATE.UNAVAILABLE],
    ]) {
      const result = await omegaFetch(`/refuse/${status}/${code}`, {
        method: "POST",
      });
      expect(result).toMatchObject({ ok: false, status, code, state });
      expect(result.message).not.toMatch(/API key/);
      expect(result.title).toBeTruthy();
    }
  });
});
