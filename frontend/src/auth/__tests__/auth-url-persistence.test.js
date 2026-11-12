import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearOnboardingRecovery,
  readAuthRedirectParams,
} from "../auth-url-intake";
import {
  flows,
  onboardingStorageKey,
  resetIntake,
  runIntake,
  stashKey,
  visit,
} from "./auth-url-fixtures";

beforeEach(() => {
  resetIntake();
  window.localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  resetIntake();
});

function enter(query) {
  visit(`/auth/jwt/register?${query}&keep=1#stay`);
  runIntake();
}
function reload() {
  window.dispatchEvent(new Event("pagehide"));
  expect(window[stashKey]).toBeUndefined();
  runIntake();
}

it("persists exactly the onboarding pair under one tab key, never session credentials or redirect metadata", () => {
  visit(
    "/sos?onboarding_token=fake-aws&onboarding_gcp_token=fake-gcp&sso_token=fake-sso&access_token=fake-access&refresh_token=fake-refresh&access=fake-sos-access&refresh=fake-sos-refresh&is_new_user=true&new_org=true&auth=saml&next=%2Fx&keep=1#stay",
  );
  runIntake();
  expect(window.location.search).toBe("?keep=1");
  expect(window.location.hash).toBe("#stay");
  expect(
    Array.from({ length: window.sessionStorage.length }, (_, index) =>
      window.sessionStorage.key(index),
    ),
  ).toEqual([onboardingStorageKey]);
  expect(
    JSON.parse(window.sessionStorage.getItem(onboardingStorageKey)),
  ).toEqual({
    pathname: "/sos",
    params: [
      ["onboarding_token", "fake-aws"],
      ["onboarding_gcp_token", "fake-gcp"],
    ],
  });
  expect(window.localStorage.length).toBe(0);
  reload();
  const recovered = readAuthRedirectParams();
  expect(recovered.get("onboarding_token")).toBe("fake-aws");
  expect(recovered.get("onboarding_gcp_token")).toBe("fake-gcp");
  for (const key of [
    "sso_token",
    "access_token",
    "refresh_token",
    "access",
    "refresh",
    "is_new_user",
    "new_org",
    "auth",
    "next",
  ]) {
    expect(recovered.get(key), key).toBeNull();
  }
  recovered.clear();
  expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
});

it.each(flows.filter(([name]) => !name.endsWith("signup")))(
  "%s never writes any credential to either storage during intake or reload recovery",
  (name, pathname, query) => {
    visit(`${pathname}?${query}`);
    runIntake();
    expect(window[stashKey], name).toBeDefined();
    expect(window.sessionStorage.length).toBe(0);
    expect(window.localStorage.length).toBe(0);
    reload();
    const recovered = readAuthRedirectParams();
    for (const key of new URLSearchParams(query).keys())
      expect(recovered.get(key)).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
    expect(window.localStorage.length).toBe(0);
  },
);

describe.each(["onboarding_token", "onboarding_gcp_token"])(
  "persisted %s",
  (key) => {
    it("keeps first-value semantics and does not expose another route's pending signup", () => {
      enter(`${key}=&${key}=fake-duplicate`);
      reload();
      visit("/auth/jwt/login");
      const unrelated = readAuthRedirectParams();
      expect(unrelated.get(key)).toBeNull();
      unrelated.clear();
      expect(
        window.sessionStorage.getItem(onboardingStorageKey),
      ).not.toBeNull();
      visit("/auth/jwt/register");
      const restored = readAuthRedirectParams();
      expect(restored.get(key)).toBe("");
      expect(Object.getOwnPropertyDescriptor(window, stashKey).enumerable).toBe(
        false,
      );
      restored.clear();
      expect(window[stashKey]).toBeUndefined();
      expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
      expect(restored.get(key)).toBeNull();
    });

    it("keeps recovery through an abandoned reader but clears it on release and full clear", () => {
      enter(`${key}=fake-onboarding`);
      const abandoned = readAuthRedirectParams();
      abandoned.clear({ retainOnboarding: true });
      expect(
        window.sessionStorage.getItem(onboardingStorageKey),
      ).not.toBeNull();
      reload();
      const restored = readAuthRedirectParams();
      expect(restored.get(key)).toBe("fake-onboarding");
      restored.release();
      expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
      expect(restored.get(key)).toBe("fake-onboarding");
      restored.clear();
      expect(restored.get(key)).toBeNull();
      reload();
      expect(readAuthRedirectParams().get(key)).toBeNull();
    });
  },
);

describe("explicit onboarding recovery cleanup", () => {
  it("clears both onboarding keys without changing session handoffs or mounted reader copies", () => {
    enter(
      "onboarding_token=fake-aws&onboarding_gcp_token=fake-gcp&sso_token=fake-session&is_new_user=true",
    );
    const source = window[stashKey];
    const mountedReader = readAuthRedirectParams();
    window.localStorage.setItem("accessToken", "fake-existing-session");
    window.sessionStorage.setItem("unrelated", "keep");

    clearOnboardingRecovery();
    clearOnboardingRecovery(); // Idempotent across multiple logout handlers.

    expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
    expect(window.sessionStorage.getItem("unrelated")).toBe("keep");
    expect(window.localStorage.getItem("accessToken")).toBe(
      "fake-existing-session",
    );
    expect(window[stashKey]).toBe(source);
    expect(Object.fromEntries(source.params)).toEqual({
      sso_token: "fake-session",
      is_new_user: "true",
    });
    const laterReader = readAuthRedirectParams();
    expect(laterReader.get("onboarding_token")).toBeNull();
    expect(laterReader.get("onboarding_gcp_token")).toBeNull();
    expect(laterReader.get("sso_token")).toBe("fake-session");
    expect(mountedReader.get("onboarding_token")).toBe("fake-aws");
    expect(mountedReader.get("onboarding_gcp_token")).toBe("fake-gcp");
  });

  it.each(["onboarding_token", "onboarding_gcp_token"])(
    "removes an onboarding-only %s stash and prevents reload recovery",
    (key) => {
      enter(`${key}=fake-onboarding`);
      expect(
        window.sessionStorage.getItem(onboardingStorageKey),
      ).not.toBeNull();
      clearOnboardingRecovery();
      expect(window[stashKey]).toBeUndefined();
      expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
      reload();
      expect(readAuthRedirectParams().get(key)).toBeNull();
    },
  );

  it.each(["getter", "removeItem"])(
    "still clears memory when sessionStorage %s throws",
    (blocked) => {
      enter("onboarding_token=fake-onboarding");
      const storage = window.sessionStorage;
      const fail = () => {
        throw new DOMException("Fake blocked storage", "SecurityError");
      };
      const getter = vi.spyOn(window, "sessionStorage", "get");
      if (blocked === "getter") getter.mockImplementation(fail);
      else getter.mockReturnValue({ removeItem: fail });
      expect(() => clearOnboardingRecovery()).not.toThrow();
      expect(window[stashKey]).toBeUndefined();
      getter.mockRestore();
      // A denied removal cannot erase storage; only the memory cleanup is guaranteed.
      expect(storage.getItem(onboardingStorageKey)).not.toBeNull();
    },
  );
});

it("an older reader cannot clear a newer redirect's tab recovery record", () => {
  enter("onboarding_token=fake-old");
  const older = readAuthRedirectParams();
  enter("onboarding_gcp_token=fake-new");
  const newer = window.sessionStorage.getItem(onboardingStorageKey);
  older.release();
  older.clear();
  expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(newer);
  reload();
  expect(readAuthRedirectParams().get("onboarding_gcp_token")).toBe("fake-new");
});

it("does not persist onboarding when replaceState fails", () => {
  visit("/auth/jwt/register?onboarding_token=fake-onboarding");
  vi.spyOn(window.history, "replaceState").mockImplementation(() => {
    throw new DOMException("Fake history rejection", "SecurityError");
  });
  expect(() => runIntake()).not.toThrow();
  expect(window[stashKey]).toBeUndefined();
  expect(window.sessionStorage.length).toBe(0);
});

it.each(["getter", "getItem", "setItem", "removeItem"])(
  "keeps the memory handoff and cleanup working with a blocked storage %s",
  (blocked) => {
    const storage = window.sessionStorage;
    const fail = () => {
      throw new DOMException("Fake blocked storage", "SecurityError");
    };
    const getter = vi.spyOn(window, "sessionStorage", "get");
    if (blocked === "getter") getter.mockImplementation(fail);
    else
      getter.mockReturnValue({
        getItem: blocked === "getItem" ? fail : storage.getItem.bind(storage),
        setItem: blocked === "setItem" ? fail : storage.setItem.bind(storage),
        removeItem:
          blocked === "removeItem" ? fail : storage.removeItem.bind(storage),
      });
    enter("onboarding_gcp_token=fake-onboarding");
    expect(window.location.search).toBe("?keep=1");
    const params = readAuthRedirectParams();
    expect(params.get("onboarding_gcp_token")).toBe("fake-onboarding");
    expect(() => params.clear()).not.toThrow();
    expect(window[stashKey]).toBeUndefined();
    expect(params.get("onboarding_gcp_token")).toBeNull();
    expect(window.localStorage.length).toBe(0);
  },
);

it.each([
  "not-json",
  "null",
  JSON.stringify({ pathname: "/auth/jwt/register", params: "invalid" }),
  JSON.stringify({
    pathname: "/auth/jwt/register",
    params: [["sso_token", "fake-session"]],
  }),
  JSON.stringify({
    pathname: "/auth/jwt/register",
    params: [["onboarding_token", { invalid: true }]],
  }),
])("ignores an invalid recovery record %s", (record) => {
  visit("/auth/jwt/register");
  window.sessionStorage.setItem(onboardingStorageKey, record);
  const params = readAuthRedirectParams();
  expect(params.get("onboarding_token")).toBeNull();
  expect(params.get("sso_token")).toBeNull();
  expect(window[stashKey]).toBeUndefined();
});
