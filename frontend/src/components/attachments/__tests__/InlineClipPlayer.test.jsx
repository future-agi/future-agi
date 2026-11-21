import { afterEach, beforeEach, it, expect, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import InlineClipPlayer from "../InlineClipPlayer";
import { clipPlacement, reservedClipValue } from "../clipPlacement";
import { resolveAttachments } from "../client";
import { spanImage, parent, projectId, readyGrant } from "./fixtures";
vi.mock("../client", () => ({ resolveAttachments: vi.fn() }));
const clip = (i, extra = {}) =>
  spanImage(i, {
    reference: {
      modality: "audio",
      mime_type: "audio/wav",
      purpose: "turn_recording",
      ...extra,
    },
  });
const a = clip(0),
  b = clip(1),
  call = clip(2, { purpose: "conversation_recording" });
const uri = (x) => `fi-attachment:${x.reference.occurrence_id}`;
const mapped = (attrs, items) => clipPlacement(attrs, items);
const at = (p, ...parts) => p.paths.get(JSON.stringify(parts));
it.each([uri(a), { uri: uri(a) }, JSON.stringify({ uri: uri(a) })])(
  "places the named URI before the duplicate carrier and keeps nested indexes",
  (v) => {
    const p = mapped(
      {
        "fi.attachment.refs": JSON.stringify([a.reference, b.reference]),
        nested: [{ clip: v }],
      },
      [a, b, call],
    );
    expect(at(p, "nested", "0", "clip")).toEqual([a]);
    expect(at(p, "fi.attachment.refs", "0")).toBeUndefined();
    expect(at(p, "fi.attachment.refs", "1")).toEqual([b]);
    expect(p.fallback).toEqual([]);
  },
);
it("does not match assets, malformed refs, a different parent projection or whole-call audio", () => {
  const p = mapped(
    {
      a: `fi-attachment:${a.reference.attachment_id}`,
      b: uri(b),
      c: uri(call),
      "fi.attachment.refs": JSON.stringify([{ ...a.reference, extra: true }]),
    },
    [a, call],
  );
  expect(p.paths.size).toBe(0);
  expect(p.fallback).toEqual([a]);
  expect(reservedClipValue(["audio"], "FI-attachment:not-a-ref")).toBe(true);
  expect(reservedClipValue(["audio"], "legacy.mp3")).toBe(false);
});
it("accepts exact ordinary projected audio only with an explicit attribute destination", () => {
  const other = clip(4, { purpose: "model_input" });
  expect(mapped({}, [other]).placed.size).toBe(0);
  expect(at(mapped({ voice: uri(other) }, [other]), "voice")).toEqual([other]);
});
function Host({ items = [a, b] }) {
  const [active, setActive] = useState(null);
  return items.map((item, i) => (
    <InlineClipPlayer
      key={i}
      projectId={projectId}
      parent={parent}
      item={item}
      label={`Turn ${i}`}
      onActivate={setActive}
      active={active === item.reference.occurrence_id}
    />
  ));
}
beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(function () {
    fireEvent.play(this);
    return Promise.resolve();
  });
  resolveAttachments.mockImplementation(async (_, req) =>
    req.map((r) => readyGrant(r.occurrence_id)),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});
it("keyboard activation grants only the selected clip, plays and releases the previous source", async () => {
  const { unmount } = render(<Host />);
  expect(resolveAttachments).not.toHaveBeenCalled();
  const user = userEvent.setup();
  const button = screen.getByRole("button", { name: "Play clip: Turn 0" });
  button.focus();
  await user.keyboard("{Enter}");
  const audio = screen.getByLabelText("Clip: Turn 0");
  await waitFor(() => expect(audio).toHaveAttribute("src"));
  expect(resolveAttachments.mock.calls[0].slice(0, 2)).toEqual([
    projectId,
    [
      {
        parent,
        occurrence_id: a.reference.occurrence_id,
        representation: "playable",
      },
    ],
  ]);
  fireEvent.loadedMetadata(audio);
  expect(screen.getByRole("button", { name: "Pause clip: Turn 0" })).toBe(
    button,
  );
  expect(button).toHaveFocus();
  await user.click(screen.getByRole("button", { name: "Play clip: Turn 1" }));
  expect(audio).not.toHaveAttribute("src");
  expect(resolveAttachments).toHaveBeenCalledTimes(2);
  unmount();
});
it("blocked autoplay keeps an enabled play button for the prepared source", async () => {
  HTMLMediaElement.prototype.play.mockRejectedValueOnce(Error());
  render(<Host />);
  const button = screen.getByRole("button", { name: "Play clip: Turn 0" });
  fireEvent.click(button);
  const audio = screen.getByLabelText("Clip: Turn 0");
  await waitFor(() => expect(audio).toHaveAttribute("src"));
  fireEvent.loadedMetadata(audio);
  await waitFor(() => expect(button).toHaveAccessibleName("Play clip: Turn 0"));
  fireEvent.click(button);
  expect(button).toHaveAccessibleName("Pause clip: Turn 0");
  expect(resolveAttachments).toHaveBeenCalledTimes(1);
});
it.each([
  ["pending", "Clip processing"],
  ["validating", "Clip processing"],
  ["partial", "Clip incomplete"],
  ["failed", "Clip unavailable"],
])("does not request %s bytes", (status, label) => {
  render(<Host items={[{ ...a, status }]} />);
  expect(screen.getByText(label)).toBeVisible();
  expect(resolveAttachments).not.toHaveBeenCalled();
});
