// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://app.example.invalid/"}
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  client: null,
  requests: [],
  scripts: [],
  loader: null,
}));
vi.mock("src/config-global", () => ({
  CURRENT_ENVIRONMENT: "production",
  POSTHOG_KEY: "fake-project-for-unit-test",
  POSTHOG_HOST: "https://analytics.example.invalid",
}));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), error: vi.fn() },
}));
vi.mock("src/utils/sessionReplayPolicy", () => ({
  SESSION_REPLAY_URL_BLOCKLIST: [],
}));
vi.mock("posthog-js", async () => {
  const { PostHog } = await import("posthog-js/lib/src/posthog-core.js");
  const { PostHogLogs } = await import("posthog-js/lib/src/posthog-logs.js");
  const { PostHogConversations } = await import(
    "posthog-js/lib/src/extensions/conversations/posthog-conversations.js"
  );
  // Use the installed loader: stubbing it would skip the supported veto hook.
  await import("posthog-js/lib/src/entrypoints/external-scripts-loader.js");
  state.loader = window.__PosthogExtensions__.loadExternalDependency;
  return {
    default: {
      init: (key, config) => {
        const ph = new PostHog();
        state.client = ph;
        ph._send_request = (request) =>
          state.requests.push(JSON.stringify(request));
        return ph.init(key, {
          ...config,
          __extensionClasses: {
            logs: PostHogLogs,
            conversations: PostHogConversations,
          },
          request_batching: false,
          autocapture: false,
        });
      },
    },
  };
});

const secret = "fake-credential-for-unit-test";
let extensionDescriptor;
let output;
let originalMethods;

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  state.client = null;
  state.requests = [];
  state.scripts = [];
  window.localStorage.clear();
  window.sessionStorage.clear();
  extensionDescriptor = Object.getOwnPropertyDescriptor(
    window,
    "__PosthogExtensions__",
  );
  // Preserve the installed loader if Node cached its CommonJS side effect.
  const loader =
    state.loader || window.__PosthogExtensions__?.loadExternalDependency;
  window.__PosthogExtensions__ = loader
    ? { loadExternalDependency: loader }
    : {};
  output = Reflect.get(window, "console");
  for (const method of ["debug", "log", "warn", "error", "info"]) {
    vi.spyOn(output, method).mockImplementation(() => {});
  }
  originalMethods = Object.fromEntries(
    ["debug", "log", "warn", "error", "info"].map((method) => [
      method,
      output[method],
    ]),
  );
  const append = Node.prototype.appendChild;
  vi.spyOn(Node.prototype, "appendChild").mockImplementation(function (node) {
    if (node.tagName === "SCRIPT") {
      state.scripts.push(node.src);
      return node; // Record the attempted request, but never load a script.
    }
    return append.call(this, node);
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Network forbidden in SDK tests");
    }),
  );
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(() => {
    throw new Error("XHR forbidden in SDK tests");
  });
  vi.stubGlobal(
    "WebSocket",
    vi.fn(() => {
      throw new Error("WebSocket forbidden in SDK tests");
    }),
  );
});
afterEach(() => {
  state.client?.stopSessionRecording();
  delete window.__PosthogExtensions__;
  if (extensionDescriptor)
    Object.defineProperty(window, "__PosthogExtensions__", extensionDescriptor);
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("installed PostHog remote extension toggles", () => {
  it.each([
    `/callback?access_token=${secret}`,
    `/shared/${secret}`,
    `/auth/jwt/invitation/accept/fake-user/${secret}`,
  ])("cannot enable uninspected logs or conversations on %s", async (path) => {
    window.history.replaceState(null, "", path);
    const { initPostHog } = await import("src/utils/PostHog/posthog");
    initPostHog();
    const ph = state.client;
    expect(ph).not.toBeNull();
    expect(ph.logs).toBeDefined();
    expect(ph.conversations).toBeDefined();
    ph._onRemoteConfig({
      logs: { captureConsoleLogs: true },
      conversations: { enabled: true, token: "fake" },
    });
    expect(
      state.scripts.filter((src) =>
        /\/static\/(logs|conversations)\.js/.test(src),
      ),
    ).toEqual([]);
    for (const [method, original] of Object.entries(originalMethods)) {
      expect(output[method]).toBe(original);
    }
    expect(JSON.stringify(state.requests)).not.toContain(secret);
    expect(ph.config.disable_conversations).toBe(true);
    // The veto is targeted, not a blanket disable of inspected SDK features.
    const script = document.createElement("script");
    script.src =
      "https://analytics.example.invalid/static/recorder.js?v=1.360.2";
    expect(ph.config.prepare_external_dependency_script(script)).toBe(script);
    for (const kind of ["logs", "conversations"]) {
      script.src = `https://analytics.example.invalid/static/${kind}.js?v=1.360.2`;
      expect(ph.config.prepare_external_dependency_script(script)).toBeNull();
    }
  });
});
