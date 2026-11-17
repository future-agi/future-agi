import { describe, expect, it } from "vitest";

import view from "../__fixtures__/my-connections-view.json";
import {
  ISSUES,
  TICKETS,
  connected,
  connection,
  disconnectUrl,
  listOf,
  notStartable,
  revoked,
  started,
} from "../__fixtures__/my-connections";

// The "Your accounts" builders against the real view's answers
// (my-connections-view.json, dumped from GET /agentcc/mcp/connect/, POST
// .../start/ and DELETE .../ through the real connect flow). Every field is
// equal, except the two times, which only need to be set or unset alike.
const TIMES = new Set(["connected_at", "rotated_at"]);

function expectSameRow(built, real) {
  expect(Object.keys(built).sort()).toEqual(Object.keys(real).sort());
  for (const key of Object.keys(real)) {
    if (TIMES.has(key)) {
      expect([key, built[key] === null]).toEqual([key, real[key] === null]);
    } else {
      expect([key, built[key]]).toEqual([key, real[key]]);
    }
  }
}

describe("the Your accounts fixtures match the view's own answers", () => {
  it("a row the reader can connect and never connected", () => {
    expectSameRow(connection(ISSUES), view.ready_never_connected);
  });

  it("the reader's active account", () => {
    expectSameRow(connected(ISSUES), view.connected);
  });

  it("an account connected without the organization's app", () => {
    expectSameRow(
      connected("moved-away", {
        start: { state: "unknown", reason: "client_not_confirmed" },
        start_url: null,
      }),
      view.connected_unknown_readiness,
    );
  });

  it("a row with no organization app: readiness unknown", () => {
    expectSameRow(
      notStartable(TICKETS, "unknown", "client_not_confirmed"),
      view.unknown_no_org_app,
    );
  });

  it("an organization app that cannot be read", () => {
    expectSameRow(
      notStartable("broken-app", "needs_admin", "oauth_client_unreadable"),
      view.needs_admin_unreadable,
    );
  });

  it("an active account on a server that changed", () => {
    expectSameRow(
      notStartable("moved-away", "unavailable", "server_changed", {
        status: "active",
        connected_at: "2026-10-26T10:00:00Z",
        disconnect_url: disconnectUrl("moved-away"),
      }),
      view.server_changed_still_active,
    );
  });

  it("a disconnected account", () => {
    expectSameRow(revoked(ISSUES), view.revoked);
  });

  it("the list, start and disconnect envelopes", () => {
    expect(Object.keys(listOf([])).sort()).toEqual(view.list_envelope_keys);
    expect(Object.keys(listOf([]).result).sort()).toEqual(
      view.list_result_keys,
    );
    expect(Object.keys(started()).sort()).toEqual(view.start_envelope_keys);
    expect(Object.keys(started().result).sort()).toEqual(
      view.start_result_keys,
    );
    expect(started().result.expires_in).toBe(view.start_expires_in);
    expect(view.disconnect_response).toEqual({
      status: true,
      result: {
        server_id: ISSUES,
        disconnected: true,
        gateway_synced: true,
        cleanup_pending: false,
        upstream_revocation: "confirmed",
      },
    });
  });
});
