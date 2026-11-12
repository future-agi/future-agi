import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearAWSOnboardingAttempt,
  prepareAWSOnboarding,
} from "./aws-onboarding";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("axios", () => ({ default: { post: mocks.post } }));
vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));

beforeEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
  clearAWSOnboardingAttempt();
  mocks.post.mockReset();
  mocks.post.mockResolvedValue({ data: { onboarding_attempt: "synthetic-attempt" } });
});

describe("AWS onboarding form capability", () => {
  it("shares one exchange across StrictMode and remounts", async () => {
    const first = prepareAWSOnboarding("synthetic-code");
    const second = prepareAWSOnboarding("synthetic-code");
    expect(second).toBe(first);
    expect(await first).toBe("synthetic-attempt");
    expect(await prepareAWSOnboarding("synthetic-code")).toBe("synthetic-attempt");
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });

  it("restores a tab's prepared attempt after module reload without spending the code again", async () => {
    await prepareAWSOnboarding("synthetic-code");
    vi.resetModules();
    const reloaded = await import("./aws-onboarding");
    expect(await reloaded.prepareAWSOnboarding("synthetic-code")).toBe("synthetic-attempt");
    expect(mocks.post).toHaveBeenCalledTimes(1);
    reloaded.clearAWSOnboardingAttempt();
  });

  it("does not resurrect tab recovery when an exchange finishes after cleanup", async () => {
    let finish;
    mocks.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const pending = prepareAWSOnboarding("synthetic-code");
    clearAWSOnboardingAttempt();
    finish({ data: { onboarding_attempt: "synthetic-attempt" } });
    await pending;
    expect(window.sessionStorage.length).toBe(0);
  });

  it("clearing an old form does not remove a newer form's recovery", async () => {
    await prepareAWSOnboarding("synthetic-old-code");
    await prepareAWSOnboarding("synthetic-new-code");
    clearAWSOnboardingAttempt("synthetic-old-code");
    expect(window.sessionStorage.getItem("fai.auth.aws-onboarding.v1")).toContain("synthetic-new-code");
  });

  it("works without session storage and never falls back to local storage", async () => {
    const writes = [];
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function blockedWrite() {
      writes.push(this);
      throw new Error("blocked");
    });
    expect(await prepareAWSOnboarding("synthetic-code")).toBe("synthetic-attempt");
    expect(writes).not.toContain(window.localStorage);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
});
