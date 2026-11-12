import { describe, expect, it } from "vitest";

import { ownAppOffer } from "../hooks/useOwnOAuthApp";

const provider = (redirectUri) => ({
  connect: {
    own_app: {
      client_group: "google",
      redirect_uri: redirectUri,
      required: true,
    },
  },
});

const callback = "/agentcc/mcp/connect/callback/";

describe("registration redirect URI transport", () => {
  it("shows a secure callback unchanged", () => {
    const uri = `https://api.example.test${callback}`;
    expect(ownAppOffer(provider(uri), "https://console.example.test")).toEqual({
      clientGroup: "google",
      redirectUri: uri,
      required: true,
    });
  });

  it.each(["localhost", "127.0.0.1", "[::1]"])(
    "allows an explicit same-origin loopback callback for %s",
    (host) => {
      const api = `http://${host}:8000`;
      expect(ownAppOffer(provider(`${api}${callback}`), api)).not.toBeNull();
    },
  );

  it.each([
    ["http://api.example.test", "http://api.example.test"],
    ["http://localhost:8000", "https://api.example.test"],
    ["http://api.example.test", "http://localhost:8000"],
    ["http://localhost:9000", "http://localhost:8000"],
    ["http://127.0.0.2:8000", "http://127.0.0.2:8000"],
    ["https://user:password@api.example.test", "http://localhost:8000"],
  ])("refuses callback origin %s on API %s", (origin, api) => {
    expect(ownAppOffer(provider(`${origin}${callback}`), api)).toBeNull();
  });
});
