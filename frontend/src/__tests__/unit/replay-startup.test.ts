import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { replayIntegration } from "@sentry-internal/replay";
import { createSentryReplay } from "src/utils/sentry-replay";
import { scrubSamlRecordingEvent } from "src/utils/saml-telemetry-urls";

vi.mock("@sentry-internal/replay", () => ({
  replayIntegration: vi.fn(() => ({ name: "Replay" })),
}));

// Exercise the shipped effect without mounting the app's route tree. Only the
// dynamic import is substituted; the separately loaded module is tested below.
const main = readFileSync("src/main.jsx", "utf8");
const start = main.indexOf("function AfterFirstPaint()");
const end = main.indexOf("const root = ReactDOM.createRoot", start);
const component = main
  .slice(start, end)
  .replace('import("./utils/sentry-replay")', "loadReplay()");

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});
afterEach(() => vi.useRealTimers());

describe("Replay after the committed frame", () => {
  function setup({ transport = true, enabled = true, existing = false } = {}) {
    let effect!: () => () => void;
    const replay = { name: "Replay" };
    const createReplay = vi.fn(() => replay);
    const loadReplay = vi.fn(async () => ({
      createSentryReplay: createReplay,
    }));
    const options = { enabled };
    const client = {
      getTransport: () => (transport ? {} : undefined),
      getOptions: () => options,
      getIntegrationByName: vi.fn(() => (existing ? replay : undefined)),
    };
    const Sentry = {
      addIntegration: vi.fn(() => {
        client.getIntegrationByName.mockReturnValue(replay);
      }),
      getClient: vi.fn((): typeof client | undefined => client),
    };
    const logger = { warn: vi.fn() };
    const mount = runInNewContext(`${component}; AfterFirstPaint;`, {
      React: { useEffect: (callback: typeof effect) => (effect = callback) },
      Sentry,
      logger,
      loadReplay,
      requestAnimationFrame,
      cancelAnimationFrame,
      setTimeout,
      clearTimeout,
    });
    mount();
    return {
      effect,
      loadReplay,
      createReplay,
      Sentry,
      client,
      options,
      logger,
    };
  }

  it("waits for the effect, animation frame and next task, then adds Replay once", async () => {
    const state = setup();
    expect(state.loadReplay).not.toHaveBeenCalled();
    state.effect();
    expect(state.loadReplay).not.toHaveBeenCalled();
    vi.advanceTimersToNextFrame();
    expect(state.loadReplay).not.toHaveBeenCalled();
    await vi.runAllTimersAsync();
    expect(state.Sentry.addIntegration).toHaveBeenCalledExactlyOnceWith({
      name: "Replay",
    });
    state.effect();
    await vi.runAllTimersAsync();
    expect(state.loadReplay).toHaveBeenCalledOnce();
    expect(state.createReplay).toHaveBeenCalledOnce();
  });

  it.each([{ transport: false }, { enabled: false }, { existing: true }])(
    "does not even load Replay for an inactive or existing client: %o",
    async (options) => {
      const state = setup(options);
      state.effect();
      await vi.runAllTimersAsync();
      expect(state.loadReplay).not.toHaveBeenCalled();
      expect(state.Sentry.addIntegration).not.toHaveBeenCalled();
    },
  );

  it("does not load Replay without a client", async () => {
    const state = setup();
    state.Sentry.getClient.mockReturnValue(undefined);
    state.effect();
    await vi.runAllTimersAsync();
    expect(state.loadReplay).not.toHaveBeenCalled();
  });

  it("cancels before the frame and after the frame but before its task", async () => {
    const first = setup();
    first.effect()();
    const second = setup();
    const cleanup = second.effect();
    vi.advanceTimersToNextFrame();
    cleanup();
    await vi.runAllTimersAsync();
    expect(first.loadReplay).not.toHaveBeenCalled();
    expect(second.loadReplay).not.toHaveBeenCalled();
  });

  it.each(["unmounted", "disabled", "replaced", "registered"])(
    "does not register when the client is %s while the chunk loads",
    async (change) => {
      const state = setup();
      let resolve!: (value: {
        createSentryReplay: typeof state.createReplay;
      }) => void;
      state.loadReplay.mockImplementationOnce(
        () => new Promise((done) => (resolve = done)),
      );
      const cleanup = state.effect();
      await vi.runAllTimersAsync();
      expect(state.loadReplay).toHaveBeenCalledOnce();
      if (change === "unmounted") cleanup();
      if (change === "disabled") state.options.enabled = false;
      if (change === "replaced")
        state.Sentry.getClient.mockReturnValue(undefined);
      if (change === "registered")
        state.client.getIntegrationByName.mockReturnValue({ name: "Replay" });
      resolve({ createSentryReplay: state.createReplay });
      await vi.runAllTimersAsync();
      expect(state.createReplay).not.toHaveBeenCalled();
      expect(state.Sentry.addIntegration).not.toHaveBeenCalled();
    },
  );

  it("contains an optional chunk load failure", async () => {
    const state = setup();
    state.loadReplay.mockRejectedValueOnce(new Error("load unavailable"));
    state.effect();
    await vi.runAllTimersAsync();
    expect(state.logger.warn).toHaveBeenCalledOnce();
    expect(state.Sentry.addIntegration).not.toHaveBeenCalled();
  });

  it("keeps the existing Replay masking and recording URL projection", () => {
    createSentryReplay();
    expect(replayIntegration).toHaveBeenCalledExactlyOnceWith({
      maskAllText: true,
      blockAllMedia: true,
      beforeAddRecordingEvent: scrubSamlRecordingEvent,
    });
    const options = vi.mocked(replayIntegration).mock.calls[0][0]!;
    expect(
      options.beforeAddRecordingEvent!({
        type: 4,
        timestamp: 0,
        data: {
          href: "https://app.invalid/dashboard?keep=1&saml_code=example",
          width: 100,
          height: 100,
        },
      }),
    ).toEqual({
      type: 4,
      timestamp: 0,
      data: {
        href: "https://app.invalid/dashboard?keep=1",
        width: 100,
        height: 100,
      },
    });
  });

  it("retains startup error/tracing monitoring and Replay sample rates", () => {
    const init = main.slice(
      main.indexOf("Sentry.init({"),
      main.indexOf("// Initialize PostHog"),
    );
    expect(init).toContain("Sentry.browserTracingIntegration(");
    expect(init).toContain("tracesSampleRate: sentryTracesSampleRate");
    expect(init).toContain("replaysSessionSampleRate: 0.1");
    expect(init).toContain("replaysOnErrorSampleRate: 1.0");
    expect(init).not.toContain("replayIntegration(");
  });
});
