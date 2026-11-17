import { describe, expect, it } from "vitest";

import {
  HOST_ONLY_NOTE,
  HOST_ONLY_STATUS,
  hostOnlyNote,
  isHostOnly,
  studioPageHref,
} from "../lib/reach";

// DEMO-FIX-2 item 1: who can reach a deployment, from EE's urlReach (DEP-02),
// or from its address when an older EE sent none.
describe("who can reach a deployment", () => {
  const local = {
    url: "http://127.0.0.1:56063",
    studioUrl: "http://127.0.0.1:56063/",
    urlReach: "deployment-host",
    urlNote: "Reachable from the deployment host only.",
  };

  it("follows EE's urlReach when it says one", () => {
    expect(isHostOnly(local)).toBe(true);
    expect(
      isHostOnly({ url: "https://a.example.com", urlReach: "network" }),
    ).toBe(false);
    // EE's word wins over the address: a loopback URL EE calls network is network.
    expect(isHostOnly({ url: "http://127.0.0.1:1", urlReach: "network" })).toBe(
      false,
    );
    expect(
      isHostOnly({ url: "https://a.example.com", urlReach: "deployment-host" }),
    ).toBe(true);
  });

  it("judges an older EE's reply by its address: loopback is host-only", () => {
    for (const url of [
      "http://127.0.0.1:55408",
      "http://127.3.2.1:9",
      "http://localhost:8080",
      "http://[::1]:7000",
    ]) {
      expect(isHostOnly({ url }), url).toBe(true);
    }
    expect(isHostOnly({ url: "https://greeter.agents.example.com" })).toBe(
      false,
    );
    expect(isHostOnly({ url: "not a url" })).toBe(false);
    expect(isHostOnly({ url: null })).toBe(false);
    expect(isHostOnly(undefined)).toBe(false);
  });

  it("says it in EE's words when EE sent them, else in ours", () => {
    expect(HOST_ONLY_STATUS).toBe("Running on this host");
    expect(hostOnlyNote(local)).toBe(
      "Reachable from the deployment host only.",
    );
    expect(hostOnlyNote({ url: local.url, urlNote: "  " })).toBe(
      HOST_ONLY_NOTE,
    );
  });

  it("never links the Studio page of a deployment only this host can reach (DEMO-POLISH-3)", () => {
    // That page asks for an access token the person may never have seen: a dead end (round-5c demo check).
    expect(studioPageHref(local)).toBeNull();
    expect(studioPageHref(local, "a+b/c")).toBeNull();
    expect(
      studioPageHref({
        url: "http://localhost:56063",
        studioUrl: "http://localhost:56063/",
      }),
    ).toBeNull();
    expect(studioPageHref(local, { tokenOnScreen: false })).toBeNull();
  });

  it("links a host-only deployment's Studio page bare only where its access token is on screen beside the link", () => {
    // The New deployment result shows the token it just minted, so the page's key gate is one paste away there;
    // no other caller sets tokenOnScreen (the detail page never does).
    expect(studioPageHref(local, { tokenOnScreen: true })).toBe(
      "http://127.0.0.1:56063/",
    );
    expect(
      studioPageHref({ ...local, studioUrl: null }, { tokenOnScreen: true }),
    ).toBeNull();
    expect(studioPageHref(undefined, { tokenOnScreen: true })).toBeNull();
  });

  it("links a network deployment's Studio page bare: never with a token, whatever it is called with (OD-21)", () => {
    const network = {
      url: "https://g.example.com",
      studioUrl: "https://g.example.com/",
      urlReach: "network",
    };
    // A caller that still passes a token gets the same bare link.
    expect(studioPageHref(network, "a+b/c")).toBe("https://g.example.com/");
    expect(
      studioPageHref({
        url: "https://g.example.com",
        studioUrl: "https://g.example.com/",
        urlReach: "network",
      }),
    ).toBe("https://g.example.com/");
    expect(studioPageHref({ ...network, studioUrl: null })).toBeNull();
    expect(studioPageHref(undefined)).toBeNull();
  });
});
