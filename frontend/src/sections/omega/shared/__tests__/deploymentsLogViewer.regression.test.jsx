import http from "node:http";
import React from "react";
import PropTypes from "prop-types";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DeploymentsApiProvider } from "src/sections/omega/deployments/api/DeploymentsApiContext";
import { createDeploymentsApi } from "src/sections/omega/deployments/api/deploymentsApi";
import {
  LOG_STATE,
  useLogStream,
} from "src/sections/omega/deployments/lib/useLogStream";

import { createOmegaClient } from "../omegaClient";
import { openOmegaStream } from "../omegaStream";
import { freshKeys } from "./sseServer.fixture";

/**
 * Regression (READINESS L06, "the Deployments log viewer is unchanged"): the
 * real log viewer hook, the real deployments client, the real shared client
 * and stream helper, against a server that writes EE's deployment log frames
 * (project-deployments/log-stream.ts: `: open`, `event: log` with seq ids,
 * `event: end`) over a real socket and cuts the response the two ways EE
 * does: a clean end at a key's expiry (before L06) and a destroyed socket
 * (after L06).
 */
const BUILD = Array.from({ length: 12 }, (_, i) => `Step ${i + 1}/12`);

async function startLogServer({ cut }) {
  const requests = [];
  let runtimeCount = 0;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    const source = url.searchParams.get("source");
    const after = Number(req.headers["last-event-id"] || 0);
    requests.push({
      source,
      after,
      tail: url.searchParams.get("tail"),
      key: req.headers.authorization,
    });
    res.writeHead(200, { "content-type": "text/event-stream; charset=utf-8" });
    res.write(": open\n\n");
    const send = (event, data, id) =>
      res.write(
        `${id === undefined ? "" : `id: ${id}\n`}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
      );
    const finish = () => (cut === "destroy" ? res.destroy() : res.end());
    if (source === "build") {
      // Each connection replays after Last-Event-ID, then cuts after 5 lines.
      const lines = BUILD.slice(after, after + 5);
      lines.forEach((line, i) =>
        send(
          "log",
          { seq: after + i + 1, at: "2026-10-17T00:00:00Z", line, source },
          after + i + 1,
        ),
      );
      if (after + lines.length >= BUILD.length) {
        send("end", { reason: "revision log closed" });
        res.end();
      } else setTimeout(finish, 30);
      return;
    }
    runtimeCount += 1;
    const tail = Number(url.searchParams.get("tail"));
    const fresh = [`request ${runtimeCount}a`, `request ${runtimeCount}b`];
    const history = tail > 0 ? ["server started", "listening on 8080"] : [];
    [...history, ...fresh].forEach((line, i) =>
      send("log", {
        at: `2026-10-17T00:00:${String(runtimeCount * 10 + i).padStart(2, "0")}Z`,
        line,
        source,
        revision: 1,
      }),
    );
    if (runtimeCount < 3) setTimeout(finish, 30);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    base: `http://127.0.0.1:${server.address().port}/omega/api`,
    requests,
    close: () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(resolve);
      }),
  };
}

function wrapperFor(base) {
  const client = createOmegaClient({
    basePath: base,
    fetchImpl: (...args) => fetch(...args),
    mintAssertion: freshKeys(),
  });
  const api = createDeploymentsApi({
    omegaJson: client.omegaJson,
    openOmegaStream: (path, options) =>
      openOmegaStream(path, {
        ...options,
        omegaFetch: client.omegaFetch,
        retryDelayMs: 20,
      }),
  });
  function Wrapper({ children }) {
    return (
      <DeploymentsApiProvider api={api}>{children}</DeploymentsApiProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
}

const servers = [];
afterEach(async () => {
  while (servers.length) await servers.pop().close();
});

describe.each(["end", "destroy"])(
  "Deployments log viewer over a real socket, cut by %s (regression)",
  (cut) => {
    it("a build log reads every line once, in order, to EE's end frame", async () => {
      const server = await startLogServer({ cut });
      servers.push(server);
      const { result, unmount } = renderHook(
        () =>
          useLogStream("dep-0123456789abcdef", {
            source: "build",
            revision: 1,
          }),
        { wrapper: wrapperFor(server.base) },
      );
      await waitFor(() => expect(result.current.state).toBe(LOG_STATE.ENDED), {
        timeout: 10_000,
      });
      expect(result.current.lines.map((l) => l.line)).toEqual(BUILD);
      expect(result.current.endReason).toBe("revision log closed");
      expect(server.requests.map((r) => r.after)).toEqual([0, 5, 10]);
      expect(new Set(server.requests.map((r) => r.key)).size).toBe(
        server.requests.length,
      );
      unmount();
    });

    it("a runtime log reopens with tail 0 and never prints a line twice", async () => {
      const server = await startLogServer({ cut });
      servers.push(server);
      const { result, unmount } = renderHook(
        () => useLogStream("dep-0123456789abcdef", { source: "runtime" }),
        { wrapper: wrapperFor(server.base) },
      );
      await waitFor(() => expect(result.current.lines).toHaveLength(8), {
        timeout: 10_000,
      });
      expect(result.current.lines.map((l) => l.line)).toEqual([
        "server started",
        "listening on 8080",
        "request 1a",
        "request 1b",
        "request 2a",
        "request 2b",
        "request 3a",
        "request 3b",
      ]);
      expect(server.requests.map((r) => r.tail)).toEqual(["200", "0", "0"]);
      expect(result.current.state).toBe(LOG_STATE.STREAMING);
      expect(result.current.error).toBeNull();
      unmount();
    });
  },
);
