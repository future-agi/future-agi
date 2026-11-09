// KH-SCOPE §6 B2: reviewer notes save as you type and survive a reload.
import { useEffect, useState } from "react";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NOTE_DRAFT_RESTORE_TRIES, useNoteDraft } from "../useNoteDraft";
import {
  NOTE_DRAFT_DEBOUNCE_MS,
  NOTE_DRAFT_TTL_MS,
  draftStatusText,
  noteDraftKey,
  serverNotesOf,
  shouldRestore,
} from "../noteDraft";

const KEY = noteDraftKey("q1", "item-1", "user-1");
const T0 = Date.parse("2026-10-23T12:00:00Z");
const NONE = { itemNotes: "", labelNotes: {}, savedAt: null };

const store = (key, value) =>
  window.localStorage.setItem(key, JSON.stringify(value));
const stored = (key = KEY) => JSON.parse(window.localStorage.getItem(key));

const mount = (initial) =>
  renderHook((props) => useNoteDraft(props), {
    initialProps: {
      queueId: "q1",
      itemId: "item-1",
      userId: "user-1",
      itemNotes: "",
      labelNotes: {},
      server: NONE,
      onRestore: vi.fn(),
      ...initial,
    },
  });

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  window.localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  window.localStorage.clear();
});

describe("save as you type", () => {
  it("writes 300 ms after the last keystroke, under the item's key", () => {
    const { result, rerender } = mount();
    rerender({
      queueId: "q1",
      itemId: "item-1",
      userId: "user-1",
      itemNotes: "Refund",
      labelNotes: {},
      server: NONE,
    });
    act(() => vi.advanceTimersByTime(NOTE_DRAFT_DEBOUNCE_MS - 1));
    expect(window.localStorage.getItem(KEY)).toBeNull();
    rerender({
      queueId: "q1",
      itemId: "item-1",
      userId: "user-1",
      itemNotes: "Refund amount wrong",
      labelNotes: { "label-a": "too high", "label-b": "" },
      server: NONE,
    });
    act(() => vi.advanceTimersByTime(NOTE_DRAFT_DEBOUNCE_MS));
    expect(KEY).toBe("fa.review.note.q1.item-1.user-1");
    expect(stored()).toEqual({
      itemNotes: "Refund amount wrong",
      labelNotes: { "label-a": "too high" },
      savedAt: T0 + 2 * NOTE_DRAFT_DEBOUNCE_MS - 1,
    });
    expect(result.current.status).toBe("saved");
    expect(draftStatusText(result.current, T0 + 1000)).toBe(
      "Draft saved · just now",
    );
    expect(draftStatusText(result.current, T0 + 2.5 * 60 * 1000)).toBe(
      "Draft saved · 2 min ago",
    );
  });

  it("typing back to the server's text drops the draft", () => {
    const server = { itemNotes: "Saved", labelNotes: {}, savedAt: T0 - 5000 };
    const { result, rerender } = mount({ itemNotes: "Saved", server });
    const props = (itemNotes) => ({
      queueId: "q1",
      itemId: "item-1",
      userId: "user-1",
      itemNotes,
      labelNotes: {},
      server,
    });
    rerender(props("Saved, edited"));
    act(() => vi.advanceTimersByTime(NOTE_DRAFT_DEBOUNCE_MS));
    expect(stored().itemNotes).toBe("Saved, edited");
    rerender(props("Saved"));
    act(() => vi.advanceTimersByTime(NOTE_DRAFT_DEBOUNCE_MS));
    expect(window.localStorage.getItem(KEY)).toBeNull();
    expect(result.current.status).toBe("idle");
  });

  it("leaving the item keeps the last keystrokes", () => {
    const { rerender } = mount();
    rerender({
      queueId: "q1",
      itemId: "item-1",
      userId: "user-1",
      itemNotes: "typed just now",
      labelNotes: {},
      server: NONE,
    });
    rerender({
      queueId: "q1",
      itemId: "item-2",
      userId: "user-1",
      itemNotes: "typed just now",
      labelNotes: {},
      server: NONE,
    });
    expect(stored().itemNotes).toBe("typed just now");
  });
});

describe("restore after a reload", () => {
  // A panel that holds the notes and, like label-panel.jsx, loads the
  // server's text in its own effects, declared after the hook: when the
  // panel mounts with the item already loaded, those run in the same commit
  // as the restore and overwrite it.
  const usePanel = () => {
    const [notes, setNotes] = useState("");
    const draft = useNoteDraft({
      queueId: "q1",
      itemId: "item-1",
      userId: "user-1",
      itemNotes: notes,
      labelNotes: {},
      server: NONE,
      onRestore: (d) => setNotes(d.itemNotes),
    });
    useEffect(() => {
      setNotes(NONE.itemNotes);
    }, []);
    return { draft, notes };
  };

  it("restores a draft when the server has no notes", () => {
    store(KEY, {
      itemNotes: "Refund amount wrong",
      labelNotes: {},
      savedAt: T0 - 60000,
    });
    const { result } = renderHook(() => usePanel());
    expect(result.current.notes).toBe("Refund amount wrong");
    expect(result.current.draft.status).toBe("restored");
    expect(draftStatusText(result.current.draft)).toBe(
      "Restored your unsaved note",
    );
    act(() => vi.advanceTimersByTime(NOTE_DRAFT_DEBOUNCE_MS * 2));
    expect(stored().itemNotes).toBe("Refund amount wrong");
    expect(result.current.draft.status).toBe("restored");
  });

  it("puts the draft back when the panel's own load overwrites it", () => {
    store(KEY, { itemNotes: "kept", labelNotes: {}, savedAt: T0 - 60000 });
    const { result } = renderHook(() => usePanel());
    expect(result.current.notes).toBe("kept");
    act(() => vi.advanceTimersByTime(NOTE_DRAFT_DEBOUNCE_MS * 2));
    expect(result.current.notes).toBe("kept");
    expect(stored().itemNotes).toBe("kept");
    expect(result.current.draft.status).toBe("restored");
  });

  it("gives up quietly (keeping the draft) when nothing takes the restore", () => {
    store(KEY, { itemNotes: "kept", labelNotes: {}, savedAt: T0 - 60000 });
    const onRestore = vi.fn();
    const { result } = mount({ onRestore });
    expect(onRestore).toHaveBeenCalledTimes(NOTE_DRAFT_RESTORE_TRIES);
    expect(result.current.status).toBe("idle");
    act(() => vi.advanceTimersByTime(NOTE_DRAFT_DEBOUNCE_MS * 2));
    expect(stored().itemNotes).toBe("kept");
  });

  it("restores only a draft newer than the server's notes", () => {
    const older = {
      itemNotes: "old draft",
      labelNotes: {},
      savedAt: T0 - 60000,
    };
    const server = {
      itemNotes: "Submitted",
      labelNotes: {},
      savedAt: T0 - 1000,
    };
    expect(shouldRestore(older, server)).toBe(false);
    expect(shouldRestore({ ...older, savedAt: T0 }, server)).toBe(true);
    store(KEY, older);
    const onRestore = vi.fn();
    mount({ onRestore, itemNotes: "Submitted", server });
    expect(onRestore).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("clears the draft once the server holds the same text", () => {
    store(KEY, {
      itemNotes: "Refund amount wrong",
      labelNotes: {},
      savedAt: T0 - 1000,
    });
    const onRestore = vi.fn();
    const { result } = mount({
      onRestore,
      itemNotes: "Refund amount wrong",
      server: serverNotesOf({
        annotations: [
          { label_id: "a", notes: "", updated_at: new Date(T0).toISOString() },
        ],
        itemNotes: "Refund amount wrong",
      }),
    });
    expect(onRestore).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(KEY)).toBeNull();
    expect(result.current.status).toBe("idle");
  });

  it("waits for the server's notes before reading or writing", () => {
    store(KEY, { itemNotes: "kept", labelNotes: {}, savedAt: T0 - 1000 });
    const onRestore = vi.fn();
    mount({ onRestore, server: null });
    act(() => vi.advanceTimersByTime(NOTE_DRAFT_DEBOUNCE_MS * 2));
    expect(onRestore).not.toHaveBeenCalled();
    expect(stored().itemNotes).toBe("kept");
  });
});

describe("storage and retention", () => {
  it("works with storage throwing and says the draft is not kept", () => {
    // setupTests.js's in-memory Storage: block it like a private window.
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    const { result, rerender } = mount();
    rerender({
      queueId: "q1",
      itemId: "item-1",
      userId: "user-1",
      itemNotes: "typed",
      labelNotes: {},
      server: NONE,
    });
    act(() => vi.advanceTimersByTime(NOTE_DRAFT_DEBOUNCE_MS));
    expect(result.current.status).toBe("unavailable");
    expect(draftStatusText(result.current)).toMatch(/not kept/);
  });

  it("evicts drafts past the 7-day TTL and never restores one", () => {
    const other = noteDraftKey("q9", "item-9", "user-1");
    store(other, {
      itemNotes: "ancient",
      labelNotes: {},
      savedAt: T0 - NOTE_DRAFT_TTL_MS - 1,
    });
    store(KEY, {
      itemNotes: "too old",
      labelNotes: {},
      savedAt: T0 - NOTE_DRAFT_TTL_MS - 1,
    });
    window.localStorage.setItem("task-draft-x", "{}");
    const onRestore = vi.fn();
    mount({ onRestore });
    expect(onRestore).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(other)).toBeNull();
    expect(window.localStorage.getItem(KEY)).toBeNull();
    expect(window.localStorage.getItem("task-draft-x")).toBe("{}");
  });

  it("is off without a queue, item or user", () => {
    expect(noteDraftKey("q1", null, "u")).toBeNull();
    const { result } = mount({ userId: null, itemNotes: "x" });
    act(() => vi.advanceTimersByTime(NOTE_DRAFT_DEBOUNCE_MS));
    expect(window.localStorage.length).toBe(0);
    expect(result.current.status).toBe("idle");
  });
});
