import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  logger: {
    error: vi.fn(),
  },
  mixpanel: {
    _recorder: null,
    get_config: vi.fn(),
    init: vi.fn(),
    set_config: vi.fn(),
    stop_session_recording: vi.fn(),
  },
}));

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("src/config-global", () => ({ MIXPANEL_HOST: "" }));
vi.mock("src/utils/logger", () => ({ default: mocks.logger }));

const BLOCKED_PATH = "/dashboard/observe/project-id/llm-tracing";

const loadMixpanel = async () => import("../mixpanel");

describe("Mixpanel session replay shutdown", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("VITE_MIXPANEL_TOKEN", "test-token");
    vi.stubEnv("VITE_MIXPANEL_SESSION_REPLAY_PERCENT", "100");
    window.__FUTURE_AGI_CONFIG__ = {};
    window.history.replaceState({}, "", "/dashboard/develop");

    mocks.logger.error.mockReset();
    mocks.mixpanel.get_config.mockReset();
    mocks.mixpanel.init.mockReset();
    mocks.mixpanel.set_config.mockReset();
    mocks.mixpanel.stop_session_recording.mockReset();
    mocks.mixpanel._recorder = {};
  });

  afterEach(() => {
    document
      .querySelectorAll("script[data-test-mixpanel-recorder]")
      .forEach((script) => script.remove());
    delete window.__FUTURE_AGI_CONFIG__;
    vi.unstubAllEnvs();
  });

  it("retries configuration after set_config throws", async () => {
    const configError = new Error("config failed");
    mocks.mixpanel.set_config.mockImplementationOnce(() => {
      throw configError;
    });
    const { syncMixpanelSessionReplay } = await loadMixpanel();

    syncMixpanelSessionReplay(BLOCKED_PATH);

    expect(mocks.mixpanel.set_config).toHaveBeenCalledTimes(1);
    expect(mocks.mixpanel.stop_session_recording).not.toHaveBeenCalled();
    expect(mocks.logger.error).toHaveBeenCalledWith(
      "Failed to stop Mixpanel session replay:",
      configError,
    );

    syncMixpanelSessionReplay(BLOCKED_PATH);

    expect(mocks.mixpanel.set_config).toHaveBeenCalledTimes(2);
    expect(mocks.mixpanel.stop_session_recording).toHaveBeenCalledTimes(1);

    syncMixpanelSessionReplay(BLOCKED_PATH);
    expect(mocks.mixpanel.set_config).toHaveBeenCalledTimes(2);
    expect(mocks.mixpanel.stop_session_recording).toHaveBeenCalledTimes(1);
  });

  it("retries recorder shutdown without repeating successful configuration", async () => {
    const stopError = new Error("stop failed");
    mocks.mixpanel.stop_session_recording.mockImplementationOnce(() => {
      throw stopError;
    });
    const { syncMixpanelSessionReplay } = await loadMixpanel();

    syncMixpanelSessionReplay(BLOCKED_PATH);

    expect(mocks.mixpanel.set_config).toHaveBeenCalledTimes(1);
    expect(mocks.mixpanel.stop_session_recording).toHaveBeenCalledTimes(1);
    expect(mocks.logger.error).toHaveBeenCalledWith(
      "Failed to stop Mixpanel session replay:",
      stopError,
    );

    syncMixpanelSessionReplay(BLOCKED_PATH);

    expect(mocks.mixpanel.set_config).toHaveBeenCalledTimes(1);
    expect(mocks.mixpanel.stop_session_recording).toHaveBeenCalledTimes(2);

    syncMixpanelSessionReplay(BLOCKED_PATH);
    expect(mocks.mixpanel.stop_session_recording).toHaveBeenCalledTimes(2);
  });

  it("allows a direct retry when deferred recorder shutdown throws", async () => {
    mocks.mixpanel._recorder = null;
    const recorderScript = document.createElement("script");
    recorderScript.dataset.testMixpanelRecorder = "true";
    recorderScript.src = "https://cdn.example.test/recorder.js";
    document.head.appendChild(recorderScript);
    mocks.mixpanel.get_config.mockReturnValue(recorderScript.src);
    const { syncMixpanelSessionReplay } = await loadMixpanel();

    syncMixpanelSessionReplay(BLOCKED_PATH);
    syncMixpanelSessionReplay(BLOCKED_PATH);

    expect(mocks.mixpanel.set_config).toHaveBeenCalledTimes(1);
    expect(mocks.mixpanel.stop_session_recording).not.toHaveBeenCalled();

    const stopError = new Error("deferred stop failed");
    mocks.mixpanel._recorder = {};
    mocks.mixpanel.stop_session_recording.mockImplementationOnce(() => {
      throw stopError;
    });
    recorderScript.dispatchEvent(new Event("load"));

    expect(mocks.mixpanel.stop_session_recording).toHaveBeenCalledTimes(1);
    expect(mocks.logger.error).toHaveBeenCalledWith(
      "Failed to stop Mixpanel session replay:",
      stopError,
    );

    syncMixpanelSessionReplay(BLOCKED_PATH);

    expect(mocks.mixpanel.set_config).toHaveBeenCalledTimes(1);
    expect(mocks.mixpanel.stop_session_recording).toHaveBeenCalledTimes(2);
  });

  it("stops a deferred recorder configured with a relative source", async () => {
    mocks.mixpanel._recorder = null;
    const recorderScript = document.createElement("script");
    recorderScript.dataset.testMixpanelRecorder = "true";
    recorderScript.src = "/assets/mixpanel-recorder.js";
    document.head.appendChild(recorderScript);
    mocks.mixpanel.get_config.mockReturnValue("/assets/mixpanel-recorder.js");
    const { syncMixpanelSessionReplay } = await loadMixpanel();

    syncMixpanelSessionReplay(BLOCKED_PATH);

    expect(mocks.mixpanel.set_config).toHaveBeenCalledTimes(1);
    expect(mocks.mixpanel.stop_session_recording).not.toHaveBeenCalled();

    mocks.mixpanel._recorder = {};
    recorderScript.dispatchEvent(new Event("load"));

    expect(mocks.mixpanel.stop_session_recording).toHaveBeenCalledTimes(1);
  });

  it("prefers the runtime replay percentage over the build setting", async () => {
    vi.stubEnv("VITE_MIXPANEL_SESSION_REPLAY_PERCENT", "0");
    window.__FUTURE_AGI_CONFIG__ = {
      VITE_MIXPANEL_SESSION_REPLAY_PERCENT: "37",
    };

    await loadMixpanel();

    expect(mocks.mixpanel.init).toHaveBeenCalledWith(
      "test-token",
      expect.objectContaining({ record_sessions_percent: 37 }),
    );
  });

  it("honors runtime blocked prefixes before Mixpanel initializes", async () => {
    window.__FUTURE_AGI_CONFIG__ = {
      VITE_MIXPANEL_SESSION_REPLAY_PERCENT: "100",
      VITE_SESSION_REPLAY_BLOCKED_PATH_PREFIXES: "/custom-heavy",
    };
    window.history.replaceState({}, "", "/custom-heavy/grid");

    await loadMixpanel();

    expect(mocks.mixpanel.init).toHaveBeenCalledWith(
      "test-token",
      expect.objectContaining({ record_sessions_percent: 0 }),
    );
  });
});
