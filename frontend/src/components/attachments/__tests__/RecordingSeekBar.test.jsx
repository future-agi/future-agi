import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import PropTypes from "prop-types";
import { useState } from "react";
import RecordingSeekBar, { waveformBars } from "../RecordingSeekBar";
import useRecordingPeaks from "../useRecordingPeaks";
import {
  projectId,
  parent,
  occurrenceId,
  readyGrant,
  standaloneManifest,
  mixedManifest,
} from "./fixtures";
import { resolveAttachments } from "../client";
vi.mock("../client", () => ({ resolveAttachments: vi.fn() }));
function Fixture({ manifest = standaloneManifest, enabled = true }) {
  const peaks = useRecordingPeaks({ projectId, parent, manifest, enabled });
  const [v, set] = useState(0);
  return (
    <RecordingSeekBar seconds={v} duration={4} onSeek={set} peaks={peaks} />
  );
}
Fixture.propTypes = { manifest: PropTypes.object, enabled: PropTypes.bool };
const descriptor = {
  v: 1,
  sample_rate: 8000,
  channels: 1,
  frame_count: 80,
  levels: [1, 10, 100].map((f) => ({ buckets: 1, frames_per_bucket: 80 * f })),
};
const reply = (
  header = JSON.stringify(descriptor),
  data = new Uint8Array([0, 128, 255, 127, 0, 128, 255, 127, 0, 128, 255, 127]),
  status = 200,
) =>
  new Response(data, {
    status,
    headers: {
      "Content-Length": "12",
      "Content-Type": "application/octet-stream",
      "X-Attachment-Peaks": header,
    },
  });
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});
it("uses separate authenticated-derivative grants and enhances the focused slider without seeking", async () => {
  let finish;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    ),
  );
  resolveAttachments.mockResolvedValue([readyGrant(occurrenceId)]);
  const { rerender } = render(<Fixture />);
  const slider = screen.getByRole("slider");
  slider.focus();
  fireEvent.change(slider, { target: { value: 1 } });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  expect(resolveAttachments.mock.calls[0][1]).toEqual([
    { parent, occurrence_id: occurrenceId, representation: "peaks" },
  ]);
  expect(fetch.mock.calls[0][1]).toMatchObject({
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
  });
  expect(fetch.mock.calls[0][1].headers).toBeUndefined();
  await act(async () => finish(reply()));
  expect(screen.getByTestId("recording-waveform")).toHaveAttribute(
    "aria-hidden",
    "true",
  );
  expect(screen.getByRole("slider")).toBe(slider);
  expect(slider).toHaveFocus();
  expect(slider).toHaveValue("1");
  rerender(<Fixture enabled={false} />);
  expect(screen.queryByTestId("recording-waveform")).toBeNull();
  fireEvent.change(slider, { target: { value: 0 } });
  expect(slider).toHaveValue("0");
});
it.each([
  () => reply(""),
  () => reply('{"v":2}'),
  () => reply(undefined, new Uint8Array(20)),
  () => reply("", new Uint8Array(), 429),
])(
  "malformed/missing/refused bytes quietly retain the plain operable slider",
  async (response) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response()));
    resolveAttachments.mockResolvedValue([readyGrant(occurrenceId)]);
    render(<Fixture />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const slider = screen.getByRole("slider");
    fireEvent.change(slider, { target: { value: 2 } });
    expect(slider).toHaveValue("2");
    expect(screen.queryByTestId("recording-waveform")).toBeNull();
  },
);
it("does not request an oversized plan and discards aborted replies", async () => {
  let finish;
  resolveAttachments.mockImplementation(
    () =>
      new Promise((r) => {
        finish = r;
      }),
  );
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const { rerender, unmount } = render(<Fixture manifest={mixedManifest} />);
  expect(resolveAttachments).not.toHaveBeenCalled();
  rerender(<Fixture />);
  const signal = resolveAttachments.mock.calls[0][2];
  unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => finish([readyGrant(occurrenceId)]));
  expect(fetch).not.toHaveBeenCalled();
});

// 512 overview columns: three loud bursts, a quiet stretch, silence and a gap.
const overview = () => {
  const columns = Array(512).fill([0, 0]);
  for (const [from, to] of [
    [40, 100],
    [180, 290],
    [330, 440],
  ])
    for (let x = from; x < to; x++) columns[x] = [-20000, 21000];
  for (let x = 120; x < 160; x++) columns[x] = [-300, 300];
  for (let x = 470; x < 500; x++) columns[x] = null;
  return columns;
};

it("draws bursts as tall bars and leaves silence and gaps empty", () => {
  const bars = waveformBars(overview(), 64);
  expect(bars).toHaveLength(64);
  expect(Math.max(...bars)).toBeCloseTo(1, 6);
  const burst = bars[30];
  const quiet = bars[17];
  expect(burst).toBeCloseTo(1, 6);
  expect(quiet).toBeGreaterThan(0);
  expect(quiet).toBeLessThan(0.2);
  expect(bars[2]).toBe(0);
  expect(bars[60]).toBe(0);
});

it("lifts quiet audio at most eightfold and draws nothing for pure silence", () => {
  expect(
    waveformBars(
      [
        [-1000, 1000],
        [0, 0],
      ],
      2,
    ),
  ).toEqual([8000 / 32767, 0]);
  expect(waveformBars(Array(8).fill([0, 0]), 4)).toEqual([0, 0, 0, 0]);
  expect(waveformBars(Array(8).fill(null), 4)).toEqual([0, 0, 0, 0]);
  expect(waveformBars([], 4)).toEqual([]);
});

it("renders the bars behind the operable slider and fills what has played", () => {
  const onSeek = vi.fn();
  render(
    <RecordingSeekBar
      seconds={3.5}
      duration={7}
      onSeek={onSeek}
      peaks={overview()}
    />,
  );
  const svg = screen.getByTestId("recording-waveform");
  const rects = [...svg.querySelectorAll("rect")];
  const heights = rects.map((r) => Number(r.getAttribute("height")));
  expect(Math.max(...heights)).toBeGreaterThan(5 * Math.min(...heights));
  const played = rects.filter((r) => r.getAttribute("fill-opacity") === "1");
  expect(played.length).toBeGreaterThan(0);
  expect(played.length).toBeLessThan(rects.length);
  for (const r of played)
    expect(Number(r.getAttribute("x"))).toBeLessThan(
      Number(svg.getAttribute("viewBox").split(" ")[2]) / 2,
    );
  const slider = screen.getByRole("slider", { name: "Recording position" });
  fireEvent.change(slider, { target: { value: 5 } });
  expect(onSeek).toHaveBeenCalledWith(5);
});
