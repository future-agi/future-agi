/**
 * The Fix tab's typewriter must not restart when streamed text is appended.
 *
 * Follow-up answers arrive as `text_delta` appends, so `fullText` changes on
 * every delta. The reveal effect reset `revealedLen` to 0 on every change,
 * which restarted the typewriter from empty on each delta — the answer block
 * collapsed to nothing and regrew continuously, churning its height for the
 * whole stream. That is a large part of the reported "UI bounces".
 *
 * An append is always a prefix-extension of the previous text, which is what
 * distinguishes it from a genuinely new message that should replay.
 */
import { act, renderHook } from "@testing-library/react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import { useStreamingText } from "../useStreamingText";

// Reveal advances on an interval; drive it deterministically.
const flush = (ms) => act(() => vi.advanceTimersByTime(ms));

describe("useStreamingText", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("keeps revealed text when a delta appends to it", () => {
    const { result, rerender } = renderHook(({ t }) => useStreamingText(t), {
      initialProps: { t: "The agent failed" },
    });

    flush(2000);
    expect(result.current.revealed).toBe("The agent failed");

    // A delta appends — the already-revealed prefix must survive.
    rerender({ t: "The agent failed to call the tool" });
    expect(result.current.revealed).not.toBe("");
    expect(
      "The agent failed to call the tool".startsWith(result.current.revealed),
    ).toBe(true);
    expect(result.current.revealed.length).toBeGreaterThanOrEqual(
      "The agent failed".length,
    );
  });

  it("never regresses to empty across a burst of deltas", () => {
    const chunks = [
      "Root",
      "Root cause",
      "Root cause: the",
      "Root cause: the retry",
      "Root cause: the retry loop",
    ];
    const { result, rerender } = renderHook(({ t }) => useStreamingText(t), {
      initialProps: { t: chunks[0] },
    });
    flush(2000);

    for (const chunk of chunks.slice(1)) {
      rerender({ t: chunk });
      // The visible block must never blank out mid-stream.
      expect(result.current.revealed.length).toBeGreaterThan(0);
      flush(2000);
      expect(result.current.revealed).toBe(chunk);
    }
  });

  it("replays from the start when the text is replaced, not appended", () => {
    const { result, rerender } = renderHook(({ t }) => useStreamingText(t), {
      initialProps: { t: "First synthesis" },
    });
    flush(2000);
    expect(result.current.revealed).toBe("First synthesis");

    // A different message is not a prefix-extension — it should type out fresh.
    rerender({ t: "A completely different answer" });
    expect(result.current.revealed).toBe("");
    flush(2000);
    expect(result.current.revealed).toBe("A completely different answer");
  });

  it("reveals instantly when asked (cached/replayed content)", () => {
    const { result } = renderHook(() =>
      useStreamingText("Already known", { instant: true }),
    );
    expect(result.current.revealed).toBe("Already known");
  });
});
