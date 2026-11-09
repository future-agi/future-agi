import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { suppressGridTyping, useRowCursor } from "../gridKeys";
import { useShortcutStore } from "../useShortcuts";

const ev = (key, mods = {}) => ({ key, ...mods });

describe("suppressGridTyping (D25)", () => {
  it("turns printable keys, Enter and Space into shortcuts while not editing", () => {
    ["?", "j", "K", "x", "1", "/", "Enter", " "].forEach((k) =>
      expect(suppressGridTyping({ event: ev(k), editing: false }), k).toBe(
        true,
      ),
    );
  });
  it("leaves F2, arrows, Tab, Escape, chords and editing alone", () => {
    ["F2", "ArrowDown", "Tab", "Escape", "Backspace"].forEach((k) =>
      expect(suppressGridTyping({ event: ev(k), editing: false }), k).toBe(
        false,
      ),
    );
    expect(
      suppressGridTyping({ event: ev("c", { metaKey: true }), editing: false }),
    ).toBe(false);
    expect(
      suppressGridTyping({ event: ev("v", { ctrlKey: true }), editing: false }),
    ).toBe(false);
    expect(suppressGridTyping({ event: ev("?"), editing: true })).toBe(false);
    expect(suppressGridTyping({})).toBe(false);
  });
});

describe("useRowCursor (D25)", () => {
  afterEach(() => vi.useRealTimers());

  it("shows no cursor at load; the first J shows it on row 1 (and does nothing else); it comes back after the sheet closes", () => {
    vi.useFakeTimers();
    const api = {
      setFocusedCell: vi.fn(),
      getAllDisplayedColumns: () => ["colA", "colB"],
      getDisplayedRowCount: () => 12,
      ensureIndexVisible: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    const ref = { current: { api } };
    const { unmount } = renderHook(() =>
      useRowCursor(ref, { surface: "grid" }),
    );
    vi.advanceTimersByTime(300);
    // iterate r3 G3 (P4): no ring / checkbox on row 1 at load.
    expect(api.setFocusedCell).not.toHaveBeenCalled();
    const later = vi.fn();
    document.addEventListener("keydown", later);
    const first = new KeyboardEvent("keydown", {
      key: "j",
      bubbles: true,
      cancelable: true,
    });
    document.body.dispatchEvent(first);
    expect(api.setFocusedCell).toHaveBeenCalledWith(0, "colA");
    expect(first.defaultPrevented).toBe(true);
    expect(later).not.toHaveBeenCalled();
    document.removeEventListener("keydown", later);
    // remember row 5, open and close the sheet
    const onFocused = api.addEventListener.mock.calls.find(
      (c) => c[0] === "cellFocused",
    )[1];
    onFocused({ rowIndex: 5, column: "colB" });
    api.setFocusedCell.mockClear();
    useShortcutStore.getState().openSheet({});
    useShortcutStore.getState().closeSheet();
    vi.advanceTimersByTime(100);
    expect(api.setFocusedCell).toHaveBeenCalledWith(5, "colB");
    unmount();
  });
});
