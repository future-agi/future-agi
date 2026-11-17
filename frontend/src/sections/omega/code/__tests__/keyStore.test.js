import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { endpoints } from "src/utils/axios";

import {
  createPanelKey,
  forgetKey,
  heldKey,
  holdKey,
  keyFor,
  panelKeyName,
  useHeldKey,
} from "../keyStore";

const PAIR = {
  key_id: "55555555-5555-4555-8555-555555555555",
  key_name: "Code snippets 2026-10-22 10:41:07",
  api_key: "c0c0c0c0c0c0c0c0c0c0c0c0c0c0wxyz",
  secret_key: "d1d1d1d1d1d1d1d1d1d1d1d1d1d1stuv",
};

const KEY_A = Object.freeze({
  apiKey: PAIR.api_key,
  secretKey: PAIR.secret_key,
  organizationId: "org-a",
  userId: "user-a",
});

describe("the code panels' key, held in this tab only", () => {
  let consoleSpies;
  beforeEach(() => {
    forgetKey();
    localStorage.clear();
    sessionStorage.clear();
    consoleSpies = /** @type {const} */ ([
      "log",
      "info",
      "warn",
      "error",
      "debug",
    ]).map((name) => vi.spyOn(console, name));
  });
  afterEach(() => {
    for (const spy of consoleSpies) spy.mockRestore();
    forgetKey();
  });

  it("creates a personal key through Future AGI's own key route, with a name that says where it came from", async () => {
    const post = vi
      .fn()
      .mockResolvedValue({ data: { status: true, result: PAIR } });
    const key = await createPanelKey({
      organizationId: "org-a",
      userId: "user-a",
      post,
      now: new Date(2026, 9, 22, 10, 41, 7),
    });
    expect(post).toHaveBeenCalledWith(endpoints.keys.generateSecretKey, {
      key_name: "Code snippets 2026-10-22 10:41:07",
    });
    // Never the organization's system key (GET /accounts/keys/): the exchange refuses it.
    expect(post.mock.calls[0][0]).not.toBe(endpoints.keys.keys);
    expect(key).toMatchObject({
      keyId: PAIR.key_id,
      name: PAIR.key_name,
      apiKey: PAIR.api_key,
      secretKey: PAIR.secret_key,
      organizationId: "org-a",
      userId: "user-a",
    });
    expect(heldKey()).toBe(key);
  });

  it("writes the key to no storage and no console", async () => {
    const post = vi.fn().mockResolvedValue({ data: { result: PAIR } });
    await createPanelKey({ organizationId: "org-a", userId: "user-a", post });
    const stored =
      JSON.stringify({ ...localStorage }) +
      JSON.stringify({ ...sessionStorage });
    expect(stored).not.toContain(PAIR.secret_key);
    expect(stored).not.toContain(PAIR.api_key);
    expect(document.cookie).not.toContain(PAIR.secret_key);
    for (const spy of consoleSpies) expect(spy).not.toHaveBeenCalled();
  });

  it("reads camelCased replies too, and refuses a reply without a usable pair", async () => {
    const camel = vi.fn().mockResolvedValue({
      data: {
        result: {
          keyId: "k",
          keyName: "n",
          apiKey: PAIR.api_key,
          secretKey: PAIR.secret_key,
        },
      },
    });
    const owner = { organizationId: "org-a", userId: "user-a" };
    expect((await createPanelKey({ ...owner, post: camel })).apiKey).toBe(
      PAIR.api_key,
    );
    forgetKey();
    const empty = vi
      .fn()
      .mockResolvedValue({ data: { result: { api_key: PAIR.api_key } } });
    await expect(createPanelKey({ ...owner, post: empty })).rejects.toThrow(
      TypeError,
    );
    expect(heldKey()).toBeNull();
    const refused = vi.fn().mockRejectedValue(new Error("403"));
    await expect(createPanelKey({ ...owner, post: refused })).rejects.toThrow(
      "403",
    );
    expect(heldKey()).toBeNull();
  });

  it("is seen by every panel of the same person until forgotten, and ignored in another organization", () => {
    const here = renderHook(() =>
      useHeldKey({ organizationId: "org-a", userId: "user-a" }),
    );
    const elsewhere = renderHook(() =>
      useHeldKey({ organizationId: "org-b", userId: "user-a" }),
    );
    expect(here.result.current).toBeNull();
    act(() => {
      holdKey({ ...KEY_A });
    });
    expect(here.result.current.apiKey).toBe(PAIR.api_key);
    expect(elsewhere.result.current).toBeNull();
    // Their own key in another organization is hidden there, not thrown away.
    expect(heldKey()).not.toBeNull();
    act(() => forgetKey());
    expect(here.result.current).toBeNull();
  });

  // POLISH-FA (CODE-PANELS verdict): KeyStrip says the key is kept "until you
  // sign out, reload or forget it". A reload evaluates this module afresh, and
  // the key lives only in its memory, so the tab comes back without it.
  it("is gone after a reload: a fresh copy of this module holds no key, and no storage has it", async () => {
    holdKey({ ...KEY_A });
    expect(heldKey()?.apiKey).toBe(PAIR.api_key);
    vi.resetModules();
    const reloaded = await import("../keyStore");
    expect(reloaded.heldKey()).toBeNull();
    const stored = JSON.stringify({ ...localStorage, ...sessionStorage });
    expect(stored).not.toContain(PAIR.secret_key);
    expect(stored).not.toContain(PAIR.api_key);
  });

  it("names keys by the minute and second they were made", () => {
    expect(panelKeyName(new Date(2026, 0, 2, 3, 4, 5))).toBe(
      "Code snippets 2026-01-02 03:04:05",
    );
  });
});

// P11 fix round 2: the key was bound to the organization only, so the next
// person to sign in to the tab in that organization was shown it and could
// reveal it. It is the creator's own now, in the organization they made it in.
describe("the held key belongs to the person who made it", () => {
  beforeEach(() => forgetKey());
  afterEach(() => forgetKey());

  it("is never held for an unknown person or organization", () => {
    for (const missing of [
      { userId: undefined },
      { userId: null },
      { userId: "" },
      { organizationId: undefined },
      { organizationId: null },
      { organizationId: "" },
    ]) {
      expect(() => holdKey({ ...KEY_A, ...missing })).toThrow(TypeError);
      expect(heldKey()).toBeNull();
    }
  });

  it.each([
    ["no organization", { userId: "user-a" }],
    ["nobody signed in", { organizationId: "org-a" }],
    ["neither", {}],
  ])("makes no key, and sends nothing, with %s", async (_, scope) => {
    const post = vi.fn().mockResolvedValue({ data: { result: PAIR } });
    await expect(createPanelKey({ ...scope, post })).rejects.toThrow(TypeError);
    expect(post).not.toHaveBeenCalled();
    expect(heldKey()).toBeNull();
  });

  it("is shown to nobody else in the same organization, and leaves the tab when they look", () => {
    act(() => {
      holdKey({ ...KEY_A });
    });
    const other = renderHook(() =>
      useHeldKey({ organizationId: "org-a", userId: "user-b" }),
    );
    expect(other.result.current).toBeNull();
    expect(heldKey()).toBeNull();
  });

  it.each([
    ["signed out (no user)", null],
    ["signed out (no user id)", undefined],
  ])("is shown to nobody when %s, and leaves the tab", (_, userId) => {
    act(() => {
      holdKey({ ...KEY_A });
    });
    const nobody = renderHook(() =>
      useHeldKey({ organizationId: "org-a", userId }),
    );
    expect(nobody.result.current).toBeNull();
    expect(heldKey()).toBeNull();
  });

  it("is not shown where the page's organization is unknown", () => {
    act(() => {
      holdKey({ ...KEY_A });
    });
    const unknown = renderHook(() =>
      useHeldKey({ organizationId: null, userId: "user-a" }),
    );
    expect(unknown.result.current).toBeNull();
    expect(keyFor(heldKey(), { userId: "user-a" })).toBeNull();
    expect(keyFor(heldKey(), { organizationId: "org-a" })).toBeNull();
    expect(
      keyFor(null, { userId: "user-a", organizationId: "org-a" }),
    ).toBeNull();
  });

  it("drops out of every panel already open when another person signs in", () => {
    let who = "user-a";
    const panel = renderHook(() =>
      useHeldKey({ organizationId: "org-a", userId: who }),
    );
    act(() => {
      holdKey({ ...KEY_A });
    });
    expect(panel.result.current?.apiKey).toBe(PAIR.api_key);
    who = "user-b";
    panel.rerender();
    expect(panel.result.current).toBeNull();
    expect(heldKey()).toBeNull();
    // And it does not come back for its maker without being made again.
    who = "user-a";
    panel.rerender();
    expect(panel.result.current).toBeNull();
  });

  it("matches a numeric user id as the auth context gives it", () => {
    act(() => {
      holdKey({ ...KEY_A, userId: 42 });
    });
    const same = renderHook(() =>
      useHeldKey({ organizationId: "org-a", userId: "42" }),
    );
    expect(same.result.current?.userId).toBe("42");
  });
});
