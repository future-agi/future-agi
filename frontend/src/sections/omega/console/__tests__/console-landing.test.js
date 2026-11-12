// The Hub handoff (Hub SPEC §7.4) and the remix landing (CP T2) as the host
// checks them before the frame is built.
import { Buffer } from "node:buffer";
import { describe, expect, it } from "vitest";

import { useThisTargets } from "src/sections/omega/hub/lib/useThis";

import {
  HUB_REF,
  REMIX_TOKEN,
  consoleLandingRedirect,
  readLandingFlag,
} from "../console-landing";

const ON = { hub: true, remix: true };
const OFF = { hub: false, remix: false };
const TOKEN = `rpl_v1.${Buffer.from(
  JSON.stringify({ c: "conv_8f2a", s: "shr_31c9", exp: 1792500000000 }),
).toString("base64url")}.${"A".repeat(43)}`;

const at = (url, flags = ON) => {
  const parsed = new URL(url, "http://localhost");
  return consoleLandingRedirect(parsed.pathname, parsed.search, flags);
};

describe("Hub handoff", () => {
  it("turns the Hub's own Use this link into the import route", () => {
    const target = useThisTargets({
      ref: "futureagi/support-triage",
      version: "1.2.0",
      capabilities: { consoleTemplate: true },
      flags: { consoleTemplate: true },
    }).find((t) => t.key === "console");
    expect(target.href).toBe(
      "/dashboard/console?hub=futureagi%2Fsupport-triage%401.2.0",
    );
    expect(at(target.href)).toBe(
      "/dashboard/console/templates/import?hub=futureagi%2Fsupport-triage%401.2.0",
    );
    // Already there: nothing to do (and a reload stays put).
    expect(
      at(
        "/dashboard/console/templates/import?hub=futureagi%2Fsupport-triage%401.2.0",
      ),
    ).toBeNull();
    expect(
      at(
        "/dashboard/console/templates/import?hub=futureagi/support-triage@1.2.0",
      ),
    ).toBe(
      "/dashboard/console/templates/import?hub=futureagi%2Fsupport-triage%401.2.0",
    );
  });

  it("drops a reference outside the Hub's grammar, or one without a version", () => {
    for (const ref of [
      "futureagi/support-triage",
      "futureagi/support-triage@latest",
      "futureagi/support-triage@1.02.0",
      "FutureAGI/support-triage@1.0.0",
      "f/support-triage@1.0.0",
      "futureagi/st@1.0.0",
      "../../etc/passwd@1.0.0",
      "futureagi/support-triage@1.0.0#x",
      "https://evil.example/x@1.0.0",
      "",
    ]) {
      expect(HUB_REF.test(ref)).toBe(false);
      expect(at(`/dashboard/console?hub=${encodeURIComponent(ref)}`)).toBe(
        "/dashboard/console",
      );
    }
    expect(at("/dashboard/console?hub=a/bbb@1.0.0&hub=c/ddd@1.0.0")).toBe(
      "/dashboard/console",
    );
  });

  it("is off unless VITE_OMEGA_HUB_CONSOLE is, and keeps other parameters", () => {
    expect(
      at("/dashboard/console?hub=futureagi%2Fsupport-triage%401.2.0&x=1", OFF),
    ).toBe("/dashboard/console?x=1");
    expect(
      at(
        "/dashboard/console/templates/import?hub=futureagi%2Fsupport-triage%401.2.0",
        OFF,
      ),
    ).toBe("/dashboard/console/templates/import");
  });

  it("leaves every other Console route alone", () => {
    expect(at("/dashboard/console")).toBeNull();
    expect(at("/dashboard/console/agents/a1?tab=runs")).toBeNull();
    expect(at("/dashboard/console/agents?hub=x")).toBeNull();
    expect(at("/dashboard/observe")).toBeNull();
  });
});

describe("remix landing", () => {
  it("keeps a well-formed share token when remix is on", () => {
    expect(REMIX_TOKEN.test(TOKEN)).toBe(true);
    expect(at(`/dashboard/console/remix/${TOKEN}`)).toBeNull();
  });

  it("lands on the Console home when remix is off, or the token is not one", () => {
    expect(at(`/dashboard/console/remix/${TOKEN}`, OFF)).toBe(
      "/dashboard/console",
    );
    for (const bad of [
      "/dashboard/console/remix",
      "/dashboard/console/remix/",
      "/dashboard/console/remix/not-a-token",
      `/dashboard/console/remix/${TOKEN}/extra`,
      `/dashboard/console/remix/rpl_v2.${TOKEN.slice(7)}`,
      `/dashboard/console/remix/${TOKEN.replace(/\.[^.]+$/, ".short")}`,
    ]) {
      expect(at(bad)).toBe("/dashboard/console");
    }
  });
});

describe("landing flags", () => {
  it("reads runtime config first, then the build, and only 1 or true", () => {
    expect(readLandingFlag("F", { F: "1" }, { F: "0" })).toBe(true);
    expect(readLandingFlag("F", { F: "" }, { F: "true" })).toBe(true);
    expect(readLandingFlag("F", {}, { F: "yes" })).toBe(false);
    expect(readLandingFlag("F", { F: "0" }, { F: "1" })).toBe(false);
    expect(readLandingFlag("F", undefined, undefined)).toBe(false);
  });
});
