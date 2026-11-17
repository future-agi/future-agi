// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://app.example.invalid/"}
import { Blob as NodeBlob } from "node:buffer";
import { CompressionStream } from "node:stream/web";
import { runInNewContext } from "node:vm";
import { gunzipSync } from "node:zlib";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import recorderSource from "mixpanel-browser/dist/mixpanel-recorder.js?raw";

const state = vi.hoisted(() => ({ config: null }));
vi.mock("mixpanel-browser", () => ({
  default: {
    init: (_key, config) => {
      state.config = config;
    },
  },
}));
vi.mock("src/config-global", () => ({ MIXPANEL_HOST: "" }));
vi.mock("src/utils/logger", () => ({
  default: { error: vi.fn(), debug: vi.fn() },
}));
vi.mock("src/utils/sessionReplayPolicy", () => ({
  isSessionReplayBlockedPath: () => false,
}));

const secret = "fake-credential-for-unit-test";
const link = `https://app.example.invalid/shared/${secret}?keep=1`;
const snapshots = [
  [
    "full snapshot",
    {
      type: 2,
      timestamp: 1,
      data: {
        node: {
          type: 2,
          id: 1,
          tagName: "a",
          attributes: { href: link },
          childNodes: [],
        },
        initialOffset: { top: 0, left: 0 },
      },
    },
  ],
  [
    "mutation snapshot",
    {
      type: 3,
      timestamp: 2,
      data: {
        source: 0,
        texts: [],
        adds: [],
        removes: [],
        attributes: [
          {
            id: 1,
            attributes: {
              href: link,
              src: `https://app.example.invalid/accounts/accept-invitation/fake-user/${secret}/`,
            },
          },
        ],
      },
    },
  ],
];
let originalRecorder;
let originalCompression;

const readBlobArrayBuffer = (blob) => {
  if (typeof blob.arrayBuffer === "function") return blob.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
};

// Execute the installed standalone bundle, not a reimplementation of its
// serializer, gzip branch or stop-time flush. Only the network sink is replaced.
const loadRecorder = (gzip) => {
  window.CompressionStream = gzip ? CompressionStream : undefined;
  runInNewContext(recorderSource, {
    window,
    document,
    navigator,
    URLSearchParams,
    btoa,
    Blob: NodeBlob,
    Response,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  });
};
const recorder = () =>
  new window.__mp_recorder({
    get_config: (key) =>
      ({
        ...state.config,
        token: "fake-project-for-unit-test",
        api_host: "https://analytics.example.invalid",
        api_routes: { record: "record" },
        ignore_dnt: true,
        opt_out_tracking_persistence_type: "localStorage",
      })[key],
    get_distinct_id: () => "fake-user",
    get_property: () => undefined,
  });

beforeEach(() => {
  vi.resetModules();
  state.config = null;
  originalRecorder = Object.getOwnPropertyDescriptor(window, "__mp_recorder");
  originalCompression = Object.getOwnPropertyDescriptor(
    window,
    "CompressionStream",
  );
  delete window.__mp_recorder;
  window.localStorage.clear();
  window.history.replaceState(null, "", "/dashboard/settings");
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "fake-project-for-unit-test");
  vi.stubEnv("VITE_MIXPANEL_SESSION_REPLAY_PERCENT", "100");
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(() => {
    throw new Error("XHR forbidden in replay tests");
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected network call in replay tests");
    }),
  );
});
afterEach(() => {
  delete window.__mp_recorder;
  if (originalRecorder)
    Object.defineProperty(window, "__mp_recorder", originalRecorder);
  delete window.CompressionStream;
  if (originalCompression)
    Object.defineProperty(window, "CompressionStream", originalCompression);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe.each(snapshots)("installed Mixpanel %s uploads", (_name, snapshot) => {
  it.each([false, true])(
    "scrubs preloaded and lazy recorders before upload (gzip=%s), including stop-time flush",
    async (gzip) => {
      loadRecorder(gzip);
      await import("src/utils/Mixpanel/mixpanel");
      expect(state.config.record_sessions_percent).toBe(100);
      for (const lazy of [false, true]) {
        if (lazy) loadRecorder(gzip);
        const instance = recorder();
        instance.recEvents = [snapshot];
        instance.batchStartTime = 1000;
        instance.replayStartTime = 1000;
        instance.replayId = "fake-replay";
        const upload = new Promise((resolve) => {
          window.fetch.mockImplementationOnce((url, options) => {
            resolve({ url, body: options.body });
            return Promise.resolve({ ok: true });
          });
        });
        // stopRecording flushes even when a route transition stops capture.
        instance.stopRecording();
        const { url, body } = await upload;
        const text = gzip
          ? gunzipSync(
              new Uint8Array(await readBlobArrayBuffer(body)),
            ).toString("utf8")
          : body;
        expect(url).toContain(`format=${gzip ? "gzip" : "body"}`);
        expect(text).not.toContain(secret);
        expect(text).toContain("auth-secret-redacted");
        expect(JSON.parse(text)[0].type).toBe(snapshot.type);
        expect(text).toContain("keep=1");
        expect(JSON.stringify(snapshot)).toContain(secret);
        expect(instance.recEvents).toEqual([]);
      }
    },
  );
});
