import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  capture: vi.fn(),
  identify: vi.fn(),
  reset: vi.fn(),
}));
vi.mock("src/utils/PostHog", () => ({
  trackPostHogEvent: mocks.capture,
  identifyPostHogUser: mocks.identify,
  resetPostHogUser: mocks.reset,
}));
import {
  Events,
  PropertyName,
  getPageViewEvent,
  handleOnDocsClicked,
  identifyUser,
  resetUser,
  trackError,
  trackEvent,
} from "..";

beforeEach(() => vi.clearAllMocks());

describe("legacy analytics API via PostHog", () => {
  it("keeps event names and properties and returns synchronously", () => {
    const properties = { [PropertyName.method]: "email", count: 2 };
    expect(trackEvent(Events.newUserSignUp, properties)).toBeUndefined();
    expect(mocks.capture).toHaveBeenCalledWith(
      Events.newUserSignUp,
      properties,
    );
  });

  it("keeps the error event naming and current-path fallback", () => {
    const properties = { error: "test failure" };
    trackError(properties, "test view");
    expect(mocks.capture).toHaveBeenLastCalledWith(
      "Client Error : test view",
      properties,
    );
    trackError(properties, undefined);
    expect(mocks.capture).toHaveBeenLastCalledWith(
      `Client Error : ${window.location.pathname}`,
      properties,
    );
  });

  it("sends only fixed custom-model enums, without reading form fields", () => {
    const properties = {
      model_provider: "azure",
      operation: "edit",
      get form_fields() {
        throw new Error("form fields must not be read");
      },
    };
    trackEvent(Events.customModelAdded, properties);
    expect(mocks.capture).toHaveBeenLastCalledWith(Events.customModelAdded, {
      model_provider: "azure",
      operation: "edit",
    });
    trackEvent(Events.customModelAdded, {
      model_provider: "entered provider name",
      operation: "entered operation",
      arbitrary: "entered value",
    });
    expect(mocks.capture).toHaveBeenLastCalledWith(Events.customModelAdded, {
      model_provider: "other",
      operation: "create",
    });
    trackEvent(Events.customModelAdded, undefined);
    expect(mocks.capture).toHaveBeenLastCalledWith(Events.customModelAdded, {
      model_provider: "other",
      operation: "create",
    });
  });

  it.each([
    "openai",
    "bedrock",
    "sagemaker",
    "azure",
    "vertex_ai",
    "custom",
    "unknown",
  ])("keeps the custom-model property boundary for %s", (provider) => {
    trackEvent(Events.customModelAdded, {
      model_provider: provider,
      operation: "edit",
      form_fields: { name: "entered name", api_key: "example" },
      arbitrary: "entered value",
    });
    expect(mocks.capture).toHaveBeenLastCalledWith(Events.customModelAdded, {
      model_provider: provider === "unknown" ? "other" : provider,
      operation: "edit",
    });
  });

  it("preserves identity and logout APIs for standalone callers", () => {
    const user = { id: "user-1" };
    expect(identifyUser(user)).toBeUndefined();
    expect(resetUser()).toBeUndefined();
    expect(mocks.identify).toHaveBeenCalledWith(user);
    expect(mocks.reset).toHaveBeenCalledOnce();
  });

  it("retains mapped deep links and documentation events", () => {
    expect(getPageViewEvent("/auth/jwt/login/")).toEqual({
      eventName: Events.signInPageVisited,
      extras: {},
    });
    expect(getPageViewEvent("/dashboard/observe/project/alerts")).toEqual({
      eventName: Events.alertHomepageLoaded,
      extras: { source: "observe_tab" },
    });
    expect(getPageViewEvent("/unknown")).toBeNull();
    handleOnDocsClicked("settings");
    expect(mocks.capture).toHaveBeenLastCalledWith(Events.docLinkClicked, {
      click: true,
      source: "settings",
    });
    handleOnDocsClicked(undefined);
    expect(mocks.capture).toHaveBeenCalledOnce();
  });
});
