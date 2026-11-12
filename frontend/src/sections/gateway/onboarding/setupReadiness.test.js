import { describe, expect, it } from "vitest";
import { setupReadiness } from "./setupReadiness";

const now = Date.parse("2026-11-09T12:00:00Z");
const off = { available: false, enabled: false, balance_usd: null };
const derive = (values = {}) =>
  setupReadiness({ providers: [], keys: [], managedStatus: off, now, ...values });

describe("setupReadiness", () => {
  it("does not complete setup for empty, successfully read lists", () => {
    expect(derive()).toEqual({ source: "not-ready", key: "not-ready", managed: "not-ready" });
  });

  it.each(["revoked", "expired", "pending", "failed"])(
    "does not count a %s key as usable", (status) => {
      expect(derive({ keys: [{ status }] }).key).toBe("not-ready");
    },
  );

  it.each(["expiresAt", "expires_at"])("checks %s against the supplied clock", (field) => {
    expect(derive({ keys: [{ status: "active", [field]: "2026-11-09T11:59:59Z" }] }).key).toBe("not-ready");
    expect(derive({ keys: [{ status: "active", [field]: "2026-11-09T12:00:00Z" }] }).key).toBe("not-ready");
    expect(derive({ keys: [{ status: "active", [field]: "2026-11-09T12:00:01Z" }] }).key).toBe("ready");
  });

  it("recognizes active keys without expiry and any usable key in a mixed list", () => {
    expect(derive({ keys: [{ status: "active", expiresAt: null }] }).key).toBe("ready");
    expect(derive({ keys: [{ status: "pending" }, { status: "active" }] }).key).toBe("ready");
  });

  it.each([undefined, null, {}, [{}], [{ status: "future-status" }], [{ status: "active", expiresAt: "invalid" }]])(
    "leaves missing or malformed key information unknown: %j", (keys) => {
      expect(derive({ keys }).key).toBe("unknown");
    },
  );

  it("requires a pushed provider, not a saved source or a health label", () => {
    expect(derive({ providers: [{ pushed: false, status: "healthy" }] }).source).toBe("not-ready");
    expect(derive({ providers: [{ name: "fixture-provider", status: "healthy" }] }).source).toBe("unknown");
    expect(derive({ providers: [{ pushed: true }] }).source).toBe("ready");
    expect(derive({ providers: { first: { pushed: false }, second: { pushed: true } } }).source).toBe("ready");
  });

  it.each([undefined, null, "invalid", [null]])("keeps unknown provider reads unknown: %j", (providers) => {
    expect(derive({ providers }).source).toBe("unknown");
  });

  it.each([
    { available: false, enabled: true, balance_usd: 20 },
    { available: true, enabled: false, balance_usd: 20 },
    { available: true, enabled: true, balance_usd: 0 },
    { available: true, enabled: true, balance_usd: -1 },
  ])("does not treat unavailable, disabled or exhausted credits as usable: %j", (managedStatus) => {
    expect(derive({ managedStatus }).source).toBe("not-ready");
    expect(derive({ managedStatus }).managed).toBe("not-ready");
  });

  it.each([undefined, null, {}, { available: true }, { enabled: true, balance_usd: 20 }])(
    "keeps unread managed status unknown: %j", (managedStatus) => {
      expect(derive({ managedStatus }).source).toBe("unknown");
    },
  );

  it.each([undefined, null, "", " ", true, [], "invalid", Infinity])(
    "does not invent a credit balance from %j", (balance_usd) => {
      expect(derive({ managedStatus: { available: true, enabled: true, balance_usd } }).source).toBe("unknown");
    },
  );

  it.each([10, "0.50"])("uses confirmed enabled credits with a positive balance: %j", (balance_usd) => {
    expect(derive({ managedStatus: { available: true, enabled: true, balance_usd } }).source).toBe("ready");
  });

  it("a confirmed source remains ready even if the other source cannot be read", () => {
    expect(derive({ providers: [{ pushed: true }], managedStatus: undefined }).source).toBe("ready");
    expect(derive({ providers: undefined, managedStatus: { available: true, enabled: true, balance_usd: 1 } }).source).toBe("ready");
  });
});
