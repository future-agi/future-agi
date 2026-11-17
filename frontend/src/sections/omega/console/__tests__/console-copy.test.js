import { describe, expect, it } from "vitest";

import { DIAGNOSIS } from "src/sections/omega/embed";

import { consoleGateCopy } from "../console-copy";
import { MINT_STATE } from "../session-assertion";

const UNREACHABLE_TITLE = "Omega Console isn't reachable from this deployment";
const MEMBER_BODY =
  "Ask your workspace admin to check the Omega Console service.";

describe("consoleGateCopy", () => {
  it("names each mint failure, and falls back to a plain reconnect", () => {
    expect(consoleGateCopy(MINT_STATE.REFUSED).needsKey.title).toBe(
      "Omega Console isn't available for your role",
    );
    expect(consoleGateCopy(MINT_STATE.SIGNED_OUT).needsKey.title).toBe(
      "Your Future AGI session has ended",
    );
    expect(consoleGateCopy(MINT_STATE.UNAVAILABLE).needsKey.title).toBe(
      "Omega Console can't sign you in right now",
    );
    expect(consoleGateCopy(null).needsKey.title).toBe(
      "Omega Console needs to reconnect",
    );
  });

  it("keeps the shared wording for causes that belong to the browser", () => {
    const { diagnosis } = consoleGateCopy(null);
    // undefined hands the gate back to the shared, cause-specific copy.
    for (const cause of [
      DIAGNOSIS.localNetworkDenied,
      DIAGNOSIS.localNetworkPrompt,
      DIAGNOSIS.safari,
      DIAGNOSIS.brave,
    ]) {
      expect(diagnosis(cause)).toBeUndefined();
    }
  });

  it("says 'not available' for every other reason the Console did not answer", () => {
    // Every such cause gets the same words: the Console isn't reachable from
    // this deployment. With no role given, the member line (no address).
    const { diagnosis } = consoleGateCopy(null);
    for (const cause of [
      DIAGNOSIS.framingBlocked,
      DIAGNOSIS.notRunning,
      DIAGNOSIS.noResponse,
      undefined,
    ]) {
      expect(diagnosis(cause)).toEqual({
        title: UNREACHABLE_TITLE,
        body: MEMBER_BODY,
      });
    }
  });

  it("names the route to fix for an admin when the Console on this site never answered", () => {
    for (const src of [
      "/omega/console/?embed=1",
      `${window.location.origin}/omega/console/?embed=1#/agents/a1`,
    ]) {
      expect(
        consoleGateCopy(null, { src, admin: true }).diagnosis(
          DIAGNOSIS.noResponse,
        ),
      ).toEqual({
        title: UNREACHABLE_TITLE,
        body: "Omega Console didn't answer at /omega/console/. If you run this deployment, route /omega/console/ to the Omega Console service, or check that the service is healthy. Then try again.",
      });
    }
  });

  it("gives a member one line to ask their workspace admin, with no address or route", () => {
    for (const src of [
      "/omega/console/?embed=1",
      "https://console.omega.futureagi.com/omega/console/?embed=1",
    ]) {
      for (const admin of [false, undefined]) {
        const { diagnosis } = consoleGateCopy(null, { src, admin });
        expect(diagnosis(DIAGNOSIS.noResponse)).toEqual({
          title: UNREACHABLE_TITLE,
          body: MEMBER_BODY,
        });
        expect(diagnosis(DIAGNOSIS.framingBlocked).body).toBe(MEMBER_BODY);
        expect(diagnosis(DIAGNOSIS.safari)).toBeUndefined();
      }
    }
  });

  it("names the Console's own address, not a route on this site, when it runs on its own origin", () => {
    const { diagnosis } = consoleGateCopy(null, {
      src: "https://console.omega.futureagi.com/omega/console/?embed=1",
      admin: true,
    });
    expect(diagnosis(DIAGNOSIS.noResponse)).toEqual({
      title: UNREACHABLE_TITLE,
      body: "Omega Console didn't answer at https://console.omega.futureagi.com/omega/console/. If you run this deployment, check that the Omega Console service there is healthy and lets this site show it in a frame. Then try again.",
    });
    expect(diagnosis(DIAGNOSIS.noResponse).body).not.toMatch(/\broute\b/);
    // Causes that belong to the browser keep the shared wording here too.
    expect(diagnosis(DIAGNOSIS.safari)).toBeUndefined();
  });
});
