// Multi-lane playback through the REAL client and media-session module, with
// the network replaced by a server double that grants one distinct path per
// requested item on whichever session the resolve names (the backend's
// exact-row selection) and can hold a session's replies.
//
// The double also models the one ownership rule these tests rely on: a media
// session belongs to the organization it was exchanged under, each project
// to one organization, and a resolve whose session is not of the project's
// organization is refused as a whole (as bound_media_session does). It is a
// stand-in, NOT proof of backend authorization; that proof belongs to the
// backend's own tests and the integrated acceptance flows.
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { resolveAttachments } from "../client";
import AttachmentMedia from "../AttachmentMedia";
import { recordingLanes, recordingPlan } from "../contract";
import { captureMediaContext, resetMediaSessions } from "../mediaSession";
import SequentialPlayback from "../SequentialPlayback";
import {
  authDefaults,
  exchangeResponse,
  gatewayPath,
  occurrenceId,
  parent,
  projectId,
} from "./fixtures";
import { stereoProjection, stereoSegments } from "./playerLane.fixtures";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), defaults: {} },
}));

const handleA = "e0000000-0000-0000-0000-0000000000a1";
const handleB = "e0000000-0000-0000-0000-0000000000b2";
const handleC = "e0000000-0000-0000-0000-0000000000c3";
const orgA = "50000000-0000-0000-0000-000000000005";
const orgB = "50000000-0000-0000-0000-0000000000b5";
// `projectId` belongs to orgA; this one to orgB.
const projectB = "10000000-0000-0000-0000-0000000000b1";
const owners = { [projectId]: orgA, [projectB]: orgB };
const isExchange = (path) => path.endsWith("attachment-media-sessions");
// /tracer/v1/projects/{project_id}/attachments/resolve
const projectOf = (path) => path.split("/")[4];
const signIn = (token, organization = orgA) =>
  Object.assign(axios.defaults, authDefaults(token, organization));

let held;
function server() {
  const handles = [handleA, handleB, handleC];
  const sessionOrganization = new Map();
  let minted = 0;
  let granted = 0;
  held = new Map();
  axios.post.mockImplementation(async (path, body, config) => {
    if (isExchange(path)) {
      const handle = handles[minted++];
      sessionOrganization.set(handle, config.headers["X-Organization-Id"]);
      return exchangeResponse(handle);
    }
    const handle = config.headers["X-Attachment-Media-Session"];
    if (held.has(handle)) await held.get(handle).promise;
    if (sessionOrganization.get(handle) !== owners[projectOf(path)])
      throw Object.assign(new Error("Request failed with status code 404"), {
        response: { status: 404 },
      });
    return {
      data: {
        items: body.items.map(({ occurrence_id }) => ({
          occurrence_id,
          status: "ready",
          gateway_path: gatewayPath(occurrence_id, `grant-${++granted}`, handle),
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
const resolveCalls = () =>
  axios.post.mock.calls.filter(([path]) => !isExchange(path));
// [bearer, organization, handle] of every resolve that left the browser.
const resolves = () =>
  resolveCalls().map(([, , config]) => [
    config.headers.Authorization,
    config.headers["X-Organization-Id"],
    config.headers["X-Attachment-Media-Session"],
  ]);
// The project every resolve that left the browser named.
const projects = () => resolveCalls().map(([path]) => projectOf(path));
const exchanges = () =>
  axios.post.mock.calls.filter(([path]) => isExchange(path)).length;

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
    // Every source the element was told to load, in order.
    sources: [],
    load: vi.fn(() => element.sources.push(element.src)),
    removeAttribute: vi.fn((name) => {
      if (name === "src") element.src = "";
    }),
  });
  return element;
}
const fire = (element, name) => element.dispatchEvent(new Event(name));
// Exactly RecordingAttachmentPlayer's construction and authorize callback.
function stereoPlayer(project = projectId) {
  const { occurrence_id, parent: owner, manifest } = stereoProjection;
  const lanes = recordingLanes(
    recordingPlan(occurrence_id, owner, manifest),
    manifest.duration_ms,
  );
  const elements = lanes.map(media);
  const player = new SequentialPlayback(
    elements,
    lanes,
    manifest.duration_ms / 1000,
    (parts, signal, options) =>
      resolveAttachments(
        project,
        parts.map((part) => part.request),
        signal,
        options,
      ),
  );
  const unavailable = vi.fn();
  player.on("unavailable", unavailable);
  return { elements, player, unavailable };
}
async function playing(player, ...elements) {
  for (const element of elements) {
    await vi.waitFor(() =>
      expect(element.load.mock.calls.length).toBeGreaterThan(
        element.loads ?? 0,
      ),
    );
    element.loads = element.load.mock.calls.length;
    fire(element, "loadedmetadata");
  }
  await vi.waitFor(() => expect(player.isPlaying()).toBe(true));
}

beforeEach(() => {
  vi.resetAllMocks();
  signIn("synthetic-access-a");
  resetMediaSessions();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("integration: multi-lane recording with operation-affine media sessions (TH-2379 player)", () => {
  it("refuses the old organization's recording once the viewer switches organization, while the new organization's own recording plays", async () => {
    server();
    const { elements, player, unavailable } = stereoPlayer();
    const [left, right] = elements;
    player.prepare();
    void player.play();
    await playing(player, left, right);
    expect(left.src).toContain(`/${handleA}/${stereoSegments[0]}/`);
    // The boundary resolve for segment 2 is in flight on A when the viewer
    // switches organization.
    const release = hold(handleA);
    fire(left, "ended");
    fire(right, "ended");
    await vi.waitFor(() => expect(resolves()).toHaveLength(2));
    await act(async () => {
      signIn("synthetic-access-a", orgB);
      captureMediaContext();
    });
    release();
    // A's late answer is discarded; the one retry under orgB, on an orgB
    // session, is refused because the project belongs to orgA.
    await vi.waitFor(() =>
      expect(unavailable).toHaveBeenCalledWith(
        expect.objectContaining({ status: "not_found" }),
      ),
    );
    // A's handle never left under orgB, and the refused retry named orgA's
    // project under orgB.
    expect(resolves()).toEqual([
      ["Bearer synthetic-access-a", orgA, handleA],
      ["Bearer synthetic-access-a", orgA, handleA],
      ["Bearer synthetic-access-a", orgB, handleB],
    ]);
    expect(projects()).toEqual([projectId, projectId, projectId]);
    // No byte of segment 2 was ever loaded, and the already-applied
    // segment 1 grants are dropped from every lane.
    expect(
      [...left.sources, ...right.sources].some((path) =>
        path.includes(stereoSegments[1]),
      ),
    ).toBe(false);
    expect([left.src, right.src]).toEqual(["", ""]);
    expect(player.isPlaying()).toBe(false);

    // Positive control: a recording of orgB's own project plays under orgB,
    // on a new session (the refused one is never reused).
    const control = stereoPlayer(projectB);
    const [controlLeft, controlRight] = control.elements;
    control.player.prepare();
    void control.player.play();
    await playing(control.player, controlLeft, controlRight);
    expect(controlLeft.src).toContain(`/${handleC}/${stereoSegments[0]}/`);
    expect(controlRight.src).toContain(`/${handleC}/${stereoSegments[0]}/`);
    expect(controlLeft.src).not.toBe(controlRight.src);
    expect(resolves()[3]).toEqual(["Bearer synthetic-access-a", orgB, handleC]);
    expect(projects()[3]).toBe(projectB);
    expect(control.unavailable).not.toHaveBeenCalled();
    control.player.destroy();
  });

  it("recovers a lane whose session cookie was evicted on ONE new session for both channels, then stops", async () => {
    server();
    const { elements, player, unavailable } = stereoPlayer();
    const [left, right] = elements;
    player.prepare();
    void player.play();
    await playing(player, left, right);
    left.currentTime = 11;
    right.currentTime = 11;
    fire(left, "timeupdate");
    // The evicted cookie refuses both lanes on that session.
    fire(right, "error");
    fire(left, "error");
    await vi.waitFor(() =>
      expect(left.src).toContain(`/${handleB}/${stereoSegments[0]}/`),
    );
    expect(right.src).toContain(`/${handleB}/${stereoSegments[0]}/`);
    expect(exchanges()).toBe(2);
    expect(resolves()).toHaveLength(2);
    expect(unavailable).not.toHaveBeenCalled();
    void player.play();
    left.currentTime = 0;
    right.currentTime = 0;
    await playing(player, left, right);
    expect([left.currentTime, right.currentTime]).toEqual([11, 11]);
    fire(left, "error");
    expect(unavailable).toHaveBeenCalledWith({
      status: "not_found",
      retryable: true,
    });
    expect(exchanges()).toBe(2);
    expect([left.src, right.src]).toEqual(["", ""]);
  });

  it("drops a renewal that lands after the session's cookie was evicted and recovers on exactly ONE new session", async () => {
    vi.useFakeTimers();
    server();
    const { elements, player, unavailable } = stereoPlayer();
    const [left, right] = elements;
    player.prepare();
    void player.play();
    await playing(player, left, right);
    left.currentTime = 11;
    right.currentTime = 11;
    fire(left, "timeupdate");
    expect(player.getCurrentTime()).toBe(11);
    // Hold session A's next resolve: the lazy renewal of both channels,
    // due 10 s before the 60 s grants lapse.
    const release = hold(handleA);
    await vi.advanceTimersByTimeAsync(51000);
    await vi.waitFor(() => expect(resolves()).toHaveLength(2));
    // A's path-scoped cookie is evicted while that renewal is out: the
    // gateway refuses both lanes on A.
    fire(right, "error");
    fire(left, "error");
    expect(player.isPlaying()).toBe(false);
    // The recovery waits for the flight it cannot interrupt...
    expect(exchanges()).toBe(1);
    expect(resolves()).toHaveLength(2);
    // ...whose late answer, grants on A, is then dropped unread.
    release();
    await vi.waitFor(() =>
      expect(left.src).toContain(`/${handleB}/${stereoSegments[0]}/`),
    );
    expect(right.src).toContain(`/${handleB}/${stereoSegments[0]}/`);
    expect(left.sources).toEqual([
      gatewayPath(stereoSegments[0], "grant-1", handleA),
      gatewayPath(stereoSegments[0], "grant-5", handleB),
    ]);
    expect(right.sources).toEqual([
      gatewayPath(stereoSegments[0], "grant-2", handleA),
      gatewayPath(stereoSegments[0], "grant-6", handleB),
    ]);
    expect(resolves()).toEqual([
      ["Bearer synthetic-access-a", orgA, handleA],
      ["Bearer synthetic-access-a", orgA, handleA],
      ["Bearer synthetic-access-a", orgA, handleB],
    ]);
    expect(exchanges()).toBe(2);
    expect(unavailable).not.toHaveBeenCalled();
    expect(player.isPlaying()).toBe(false);
    expect(player.getCurrentTime()).toBe(11);
    // Play resumes at the kept position on the new session.
    void player.play();
    left.currentTime = 0;
    right.currentTime = 0;
    await playing(player, left, right);
    expect([left.currentTime, right.currentTime]).toEqual([11, 11]);
    // That was the one recovery: the next refusal leaves Retry.
    fire(left, "error");
    expect(unavailable).toHaveBeenCalledWith({
      status: "not_found",
      retryable: true,
    });
    expect(exchanges()).toBe(2);
    expect(resolves()).toHaveLength(3);
    expect([left.src, right.src]).toEqual(["", ""]);
  });
});

describe("integration: inline media recovers an evicted cookie once (TH-2379 player)", () => {
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  });
  afterEach(() => vi.restoreAllMocks());

  it("reauthorizes on a new session in place, then leaves the next failure to Retry", async () => {
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
        request={{ occurrence_id: occurrenceId, parent, representation: "playable" }}
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
    fireEvent.error(element);
    // The refused grant is dropped at once; the element stays in place.
    expect(audio()).toBe(element);
    await waitFor(() =>
      expect(element.getAttribute("src")).toContain(`/${handleB}/`),
    );
    expect(exchanges()).toBe(2);
    element.currentTime = 0;
    fireEvent(element, new Event("loadedmetadata"));
    expect(element.currentTime).toBe(12);
    expect(play).toHaveBeenCalledTimes(1);
    fireEvent.error(element);
    expect(await screen.findByText("Retry")).toBeInTheDocument();
    expect(exchanges()).toBe(2);
  });
});
