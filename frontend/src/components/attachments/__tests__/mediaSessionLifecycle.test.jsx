// Handle lifecycle through the real client, hook and sequential player, with
// the network replaced by a server double that grants paths on whichever
// session the resolve names (the backend's exact-row selection).
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { resetUserStore } from "src/utils/auth/userStoreResets";
import { resolveAttachments } from "../client";
import AttachmentMedia from "../AttachmentMedia";
import { recordingPlan } from "../contract";
import { captureMediaContext, resetMediaSessions } from "../mediaSession";
import SequentialPlayback from "../SequentialPlayback";
import useAttachmentGrants from "../useAttachmentGrants";
import {
  authDefaults,
  exchangeResponse,
  gatewayPath,
  occurrenceId,
  parent,
  projectId,
} from "./fixtures";
import { pipecatProjection } from "./pipecatRecording";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), defaults: {} },
}));

const [first, second] = pipecatProjection.manifest.segments.map(
  (segment) => segment.occurrence_id,
);
const handleA = "e0000000-0000-0000-0000-0000000000a1";
const handleB = "e0000000-0000-0000-0000-0000000000b2";
const handleC = "e0000000-0000-0000-0000-0000000000c3";
const requests = [
  { occurrence_id: occurrenceId, parent, representation: "playable" },
];
const isExchange = (path) => path.endsWith("attachment-media-sessions");
const signIn = (token) => Object.assign(axios.defaults, authDefaults(token));

let held;
function server({ handles = [handleA, handleB, handleC], lifetimeMs } = {}) {
  let minted = 0;
  held = new Map();
  axios.post.mockImplementation(async (path, body, config) => {
    if (isExchange(path))
      return exchangeResponse(handles[minted++], lifetimeMs);
    const handle = config.headers["X-Attachment-Media-Session"];
    if (held.has(handle)) await held.get(handle).promise;
    return {
      data: {
        items: body.items.map(({ occurrence_id }) => ({
          occurrence_id,
          status: "ready",
          gateway_path: gatewayPath(occurrence_id, `grant-${minted}`, handle),
          expires_at: new Date(Date.now() + 60000).toISOString(),
        })),
      },
    };
  });
}
function hold(handle) {
  let release;
  const promise = new Promise((done) => {
    release = done;
  });
  held.set(handle, { promise, release });
  return release;
}
const sentHandles = () =>
  axios.post.mock.calls
    .filter(([path]) => !isExchange(path))
    .map(([, , config]) => [
      config.headers.Authorization,
      config.headers["X-Attachment-Media-Session"],
    ]);
const exchangeCount = () =>
  axios.post.mock.calls.filter(([path]) => isExchange(path)).length;

beforeEach(() => {
  vi.resetAllMocks();
  signIn("synthetic-access-a");
  resetMediaSessions();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("integration: grant hook keyed by the tab's auth context", () => {
  it("drops a refreshed context's grants at once and resolves again under the new bearer", async () => {
    server();
    const { result } = renderHook(() =>
      useAttachmentGrants(projectId, requests, { renew: true }),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.results[0].gateway_path).toContain(`/${handleA}/`);
    const release = hold(handleB);
    await act(async () => {
      signIn("synthetic-access-b"); // the 401 interceptor's setSession
      captureMediaContext(); // any attachment request in the tab notices
    });
    // No stale source while the new context resolves.
    expect(result.current).toMatchObject({ status: "loading", results: [] });
    await act(async () => release());
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.results[0].gateway_path).toContain(`/${handleB}/`);
    expect(sentHandles()).toEqual([
      ["Bearer synthetic-access-a", handleA],
      ["Bearer synthetic-access-b", handleB],
    ]);
  });

  it("never applies the previous context's late reply", async () => {
    server();
    const releaseA = hold(handleA);
    const { result } = renderHook(() =>
      useAttachmentGrants(projectId, requests),
    );
    await waitFor(() => expect(sentHandles()).toHaveLength(1));
    await act(async () => {
      signIn("synthetic-access-b");
      captureMediaContext();
    });
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.results[0].gateway_path).toContain(`/${handleB}/`);
    await act(async () => releaseA());
    await act(async () => {});
    expect(result.current.results[0].gateway_path).toContain(`/${handleB}/`);
    // A's handle was only ever sent under A's bearer.
    expect(sentHandles()).not.toContainEqual([
      "Bearer synthetic-access-b",
      handleA,
    ]);
  });

  it("clears grants and handles on logout", async () => {
    server();
    const { result } = renderHook(() =>
      useAttachmentGrants(projectId, requests),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await act(async () => {
      axios.defaults.headers.common = {}; // setSession(null)
      resetUserStore("attachment-media"); // resetAllUserStores()
    });
    await waitFor(() => expect(result.current.status).toBe("unavailable"));
    expect(result.current.results).toEqual([]);
    expect(axios.post).toHaveBeenCalledTimes(2);
  });

  it("asks for a new media session when the viewer retries", async () => {
    server();
    const { result } = renderHook(() =>
      useAttachmentGrants(projectId, requests),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    // e.g. the image failed to load because its session cookie was evicted.
    act(() => result.current.retry());
    await waitFor(() =>
      expect(result.current.results[0]?.gateway_path).toContain(`/${handleB}/`),
    );
    expect(exchangeCount()).toBe(2);
  });

  it("does not keep forcing new sessions after one Retry", async () => {
    server();
    const other = [
      {
        occurrence_id: "20000000-0000-0000-0000-0000000000f2",
        parent,
        representation: "playable",
      },
    ];
    const { result, rerender } = renderHook(
      ({ items }) => useAttachmentGrants(projectId, items),
      { initialProps: { items: requests } },
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    act(() => result.current.retry());
    await waitFor(() =>
      expect(result.current.results[0]?.gateway_path).toContain(`/${handleB}/`),
    );
    // A later request (zoom, download, another image) reuses B's session.
    rerender({ items: other });
    await waitFor(() =>
      expect(result.current.results[0]?.occurrence_id).toBe(
        other[0].occurrence_id,
      ),
    );
    expect(result.current.results[0].gateway_path).toContain(`/${handleB}/`);
    expect(exchangeCount()).toBe(2);
  });
});

describe("integration: inline media after an auth context change", () => {
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  });
  afterEach(() => vi.restoreAllMocks());

  it("drops the withdrawn source at once and resumes the replacement in place", async () => {
    server();
    const item = {
      reference: {
        attachment_id: "a0000000-0000-0000-0000-00000000000a",
        occurrence_id: occurrenceId,
        direction: "mixed",
        modality: "audio",
        mime_type: "audio/wav",
      },
      status: "ready",
      capture_complete: true,
    };
    const { container } = render(
      <AttachmentMedia
        projectId={projectId}
        request={requests[0]}
        item={item}
        label="Call recording"
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Load audio: Call recording" }),
    );
    const audio = () => container.querySelector("audio");
    await waitFor(() =>
      expect(audio()?.getAttribute("src")).toContain(`/${handleA}/`),
    );
    const element = audio();
    Object.defineProperty(element, "currentTime", {
      value: 12,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(element, "paused", {
      value: false,
      configurable: true,
    });
    const play = HTMLMediaElement.prototype.play;
    play.mockClear();
    const release = hold(handleB);
    await act(async () => {
      signIn("synthetic-access-b");
      captureMediaContext();
    });
    // The previous context's grant is no longer streamed while B resolves.
    expect(audio()).toBe(element);
    expect(element.getAttribute("src")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Preparing audio");
    await act(async () => release());
    await waitFor(() =>
      expect(element.getAttribute("src")).toContain(`/${handleB}/`),
    );
    element.currentTime = 0;
    fireEvent(element, new Event("loadedmetadata"));
    expect(element.currentTime).toBe(12);
    expect(play).toHaveBeenCalledTimes(1);
  });
});

function media() {
  const element = new EventTarget();
  Object.assign(element, {
    src: "",
    preload: "none",
    currentTime: 0,
    readyState: 4,
    seeking: false,
    play: vi.fn().mockResolvedValue(),
    pause: vi.fn(),
    load: vi.fn(),
    removeAttribute: vi.fn((name) => {
      if (name === "src") element.src = "";
    }),
  });
  return element;
}
const fire = (element, name) => element.dispatchEvent(new Event(name));
function player() {
  const { parts } = recordingPlan(
    pipecatProjection.occurrence_id,
    pipecatProjection.parent,
    pipecatProjection.manifest,
  );
  const element = media();
  // Exactly RecordingAttachmentPlayer's authorize callback.
  const sequence = new SequentialPlayback(
    element,
    parts,
    pipecatProjection.manifest.duration_ms / 1000,
    async (part, signal, options) => {
      const [result] = await resolveAttachments(
        projectId,
        [part.request],
        signal,
        options,
      );
      return result;
    },
  );
  return { element, sequence };
}
async function playing(element, sequence) {
  await vi.waitFor(() =>
    expect(element.load.mock.calls.length).toBeGreaterThan(element.loads ?? 0),
  );
  element.loads = element.load.mock.calls.length;
  fire(element, "loadedmetadata");
  await vi.waitFor(() => expect(sequence.isPlaying()).toBe(true));
}

describe("integration: sequential recording with independent media sessions", () => {
  it("keeps player A on its live grant while B exchanges and resolves", async () => {
    server();
    const { element, sequence } = player();
    sequence.prepare();
    void sequence.play();
    await playing(element, sequence);
    const live = element.src;
    expect(live).toContain(`/${handleA}/${first}/`);
    element.currentTime = 12;
    fire(element, "timeupdate");
    const loads = element.load.mock.calls.length;
    const pauses = element.pause.mock.calls.length;
    // Another image/segment/tab mints a NEW session and resolves on it.
    const [image] = await resolveAttachments(
      projectId,
      [{ occurrence_id: occurrenceId, parent, representation: "original" }],
      undefined,
      { fresh: true },
    );
    expect(image.gateway_path).toContain(`/${handleB}/`);
    // A's source, position and play state are untouched: its own cookie
    // path still carries A's credential, so nothing needs re-authorizing.
    expect(element.src).toBe(live);
    expect(element.load.mock.calls.length).toBe(loads);
    expect(element.pause.mock.calls.length).toBe(pauses);
    expect(sequence.isPlaying()).toBe(true);
    expect(sequence.getCurrentTime()).toBe(12);
    // The next segment is granted on the tab's usable session, whichever.
    fire(element, "ended");
    await vi.waitFor(() =>
      expect(element.src).toContain(`/${handleB}/${second}/`),
    );
    await playing(element, sequence);
    expect(sequence.getCurrentTime()).toBe(30);
    sequence.destroy();
  });

  it("renews onto a replacement session, keeping the seek position and play state", async () => {
    vi.useFakeTimers();
    // Sessions that can back only one renewal: the second grant needs a new one.
    server({ lifetimeMs: 100000 });
    const { element, sequence } = player();
    sequence.prepare();
    void sequence.play();
    await playing(element, sequence);
    expect(element.src).toContain(`/${handleA}/`);
    sequence.setTime(20);
    expect(element.currentTime).toBe(20);
    await vi.advanceTimersByTimeAsync(49000);
    expect(exchangeCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(2000);
    await vi.waitFor(() => expect(element.src).toContain(`/${handleB}/`));
    // A's grant was replaced in place on session B; B never extended A.
    expect(exchangeCount()).toBe(2);
    element.currentTime = 0;
    await playing(element, sequence);
    expect(element.currentTime).toBe(20);
    expect(sequence.getCurrentTime()).toBe(20);
    expect(sentHandles().map(([, handle]) => handle)).toEqual([
      handleA,
      handleB,
    ]);
    sequence.destroy();
  });

  it("survives a token refresh mid-recording: the next segment pairs under the new bearer", async () => {
    server();
    const { element, sequence } = player();
    sequence.prepare();
    void sequence.play();
    await playing(element, sequence);
    element.currentTime = 29;
    fire(element, "timeupdate");
    signIn("synthetic-access-b");
    fire(element, "ended");
    await vi.waitFor(() =>
      expect(element.src).toContain(`/${handleB}/${second}/`),
    );
    await playing(element, sequence);
    expect(sentHandles()).toEqual([
      ["Bearer synthetic-access-a", handleA],
      ["Bearer synthetic-access-b", handleB],
    ]);
    sequence.destroy();
  });
});
