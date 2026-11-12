// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://app.example.invalid/"}
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { gunzipSync, strFromU8, strToU8 } from "fflate";
import { LazyLoadedSessionRecording } from "posthog-js/lib/src/extensions/replay/external/lazy-loaded-session-recorder.js";

const state = vi.hoisted(() => ({ config: null }));
vi.mock("posthog-js", () => ({
  default: {
    init: (_key, config) => {
      state.config = config;
    },
  },
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

const secret = "fake-invitation-credential-for-unit-test";
const invitation = `https://app.example.invalid/auth/jwt/invitation/accept/fake-user/${secret}`;
const snapshots = [
  [
    "full DOM",
    {
      type: 2,
      timestamp: 1,
      data: {
        node: {
          type: 2,
          id: 1,
          tagName: "input",
          attributes: { value: invitation },
          childNodes: [{ type: 3, id: 2, textContent: invitation }],
        },
        initialOffset: { top: 0, left: 0 },
      },
    },
  ],
  [
    "DOM mutation",
    {
      type: 3,
      timestamp: 2,
      data: {
        source: 0,
        texts: [{ id: 2, value: invitation }],
        attributes: [{ id: 1, attributes: { value: invitation } }],
        adds: [
          {
            parentId: 1,
            nextId: null,
            node: { type: 3, id: 3, textContent: invitation },
          },
        ],
        removes: [],
      },
    },
  ],
];
const decode = (event) => {
  if (!event.cv) return event;
  const unpack = (value) =>
    JSON.parse(strFromU8(gunzipSync(strToU8(value, true))));
  return {
    ...event,
    data:
      event.type === 2
        ? unpack(event.data)
        : {
            ...event.data,
            texts: unpack(event.data.texts),
            attributes: unpack(event.data.attributes),
            adds: unpack(event.data.adds),
            removes: unpack(event.data.removes),
          },
  };
};
const loadConfig = async () => {
  const { initPostHog } = await import("src/utils/PostHog/posthog");
  initPostHog();
  expect(state.config).not.toBeNull();
  return state.config;
};
// Exercise installed onRRwebEmit/compressEvent/_captureSnapshot. Stub only the
// session lifecycle, scheduling and I/O; do not reproduce its compressor.
const emit = (config, rawEvent) => {
  const packed = [];
  const sent = [];
  const recorder = Object.create(LazyLoadedSessionRecording.prototype);
  Object.assign(recorder, {
    _instance: {
      config,
      get_property: () => ({}),
      requestRouter: {
        endpointFor: () => "https://analytics.example.invalid/s/",
      },
      capture: (event, properties) => {
        const clean = config.before_send({ event, properties });
        if (clean) sent.push(clean);
      },
    },
    _sessionId: "fake-session",
    _windowId: "fake-window",
    _fullSnapshotTimestamps: [],
    _processQueuedEvents: vi.fn(),
    _pageViewFallBack: vi.fn(),
    _scheduleFullSnapshot: vi.fn(),
    _updateWindowAndSessionIds: vi.fn(),
    _urlTriggerMatching: {
      checkUrlTriggerConditions: vi.fn(),
      urlBlocked: false,
    },
    _triggerMatching: { triggerStatus: () => "trigger_activated" },
    _statusMatcher: () => "active",
    _captureSnapshotBuffered: (properties) => {
      packed.push(properties.$snapshot_data);
      recorder._captureSnapshot({
        ...properties,
        $snapshot_data: [properties.$snapshot_data],
      });
    },
  });
  recorder.onRRwebEmit(rawEvent);
  return { packed, sent, masking: recorder._masking };
};

beforeEach(() => {
  vi.resetModules();
  state.config = null;
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/dashboard/settings");
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Network forbidden in replay tests");
    }),
  );
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(() => {
    throw new Error("XHR forbidden in replay tests");
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe.each(snapshots)(
  "installed PostHog %s invitation snapshots",
  (_label, snapshot) => {
    it("keeps replay usable on an ordinary settings page without sending invitation credentials", async () => {
      const config = await loadConfig();
      const { packed, sent, masking } = emit(config, snapshot);
      expect(packed).toHaveLength(1);
      expect(JSON.stringify(decode(packed[0]))).toContain(secret);
      expect(sent).toHaveLength(1);
      const decoded = sent[0].properties.$snapshot_data.map(decode);
      expect(JSON.stringify(decoded)).not.toContain(secret);
      expect(JSON.stringify(decoded)).toContain("auth-secret-redacted");
      expect(config.disable_session_recording).toBe(false);
      expect(config.session_recording.compress_events).toBe(false);
      expect(masking.maskAllInputs).toBe(true);
      expect(masking.maskTextSelector).toBe("*");
    });

    it("drops unexpectedly compressed snapshots rather than trusting an opaque payload", async () => {
      const config = await loadConfig();
      const { packed, sent } = emit(
        {
          ...config,
          session_recording: {
            ...config.session_recording,
            compress_events: true,
          },
        },
        snapshot,
      );
      expect(packed).toHaveLength(1);
      expect(packed[0].cv).toBe("2024-10");
      expect(JSON.stringify(decode(packed[0]))).toContain(secret);
      expect(sent).toHaveLength(0);
    });
  },
);
