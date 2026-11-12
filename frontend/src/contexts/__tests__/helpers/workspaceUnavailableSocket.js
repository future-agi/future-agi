import { waitFor } from "@testing-library/react";
import { expect, vi } from "vitest";

// Keep the real ticket builder and Falcon socket hook. Only the two network
// boundaries are offline; workspace scope belongs to the ticket POST now.
export function installWorkspaceSocketTransport() {
  const sockets = [];
  const tickets = new Map();
  let sequence = 0;

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, options) => {
      if (
        url !== "https://offline.invalid/accounts/handoff/ws-ticket/" ||
        options?.method !== "POST" ||
        options.headers?.Authorization !== "Bearer offline"
      ) {
        throw new Error("Unexpected offline socket ticket request");
      }
      const request = JSON.parse(options.body);
      if (request.path !== "/ws/falcon-ai/") {
        throw new Error("Unexpected offline socket path");
      }
      const ticket = `workspace_fixture_${++sequence}`.padEnd(43, "_");
      tickets.set(ticket, request);
      return { ok: true, json: async () => ({ ticket }) };
    }),
  );

  class OfflineSocket {
    static OPEN = 1;
    static CONNECTING = 0;
    readyState = 1;
    send = vi.fn();
    close() {
      this.readyState = 3;
    }
    constructor(url) {
      const ticket = new URL(url).searchParams.get("ticket");
      const request = tickets.get(ticket);
      if (!request) throw new Error("Socket did not use an issued test ticket");
      tickets.delete(ticket);
      this.url = url;
      // Inspect the scope of this socket's ticket, not merely the latest POST.
      this.workspaceId = request.query?.workspace_id;
      sockets.push(this);
    }
  }
  vi.stubGlobal("WebSocket", OfflineSocket);

  async function waitForSocket(workspaceId) {
    await waitFor(() => {
      expect(sockets.at(-1)?.readyState).toBe(OfflineSocket.OPEN);
      expect(sockets.at(-1)?.onmessage).toBeTypeOf("function");
      expect(sockets.at(-1)?.workspaceId).toBe(workspaceId);
    });
    return sockets.at(-1);
  }

  return { sockets, waitForSocket };
}
