import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderHook } from "@testing-library/react";
import { actionForEvent } from "src/utils/keymap";
import useKeyboardShortcuts from "../annotate/use-keyboard-shortcuts";

const press = (key, target = document.body) =>
  fireEvent.keyDown(target, { key, bubbles: true });

// iterate r3 G3 merge: the `?` sheet on the review surface shows J / ↓ and
// K / ↑ (keymap.js); the workspace's own hook keeps ← / → and moves on
// ↓ / ↑ too, except inside a widget that uses vertical arrows itself.
describe("review workspace arrows", () => {
  it("↓ / ↑ move items like J / K; ← / → still do", () => {
    expect(
      actionForEvent(
        new KeyboardEvent("keydown", { key: "ArrowDown" }),
        "review",
      ),
    ).toBe("next");
    const onNext = vi.fn();
    const onPrev = vi.fn();
    renderHook(() => useKeyboardShortcuts({ onNext, onPrev }));
    press("ArrowDown");
    press("ArrowUp");
    press("ArrowRight");
    press("ArrowLeft");
    expect(onNext).toHaveBeenCalledTimes(2);
    expect(onPrev).toHaveBeenCalledTimes(2);
  });

  it("leaves ↓ / ↑ to radio groups, trees and handled events", () => {
    const onNext = vi.fn();
    renderHook(() => useKeyboardShortcuts({ onNext, onPrev: vi.fn() }));
    const group = document.createElement("div");
    group.setAttribute("role", "radiogroup");
    const choice = document.createElement("button");
    choice.setAttribute("role", "radio");
    group.append(choice);
    document.body.append(group);
    choice.focus();
    press("ArrowDown", choice);
    group.remove();

    const tree = document.createElement("div");
    tree.setAttribute("role", "tree");
    tree.tabIndex = 0;
    document.body.append(tree);
    tree.focus();
    press("ArrowDown", tree);
    tree.remove();

    const handled = document.createElement("div");
    handled.tabIndex = 0;
    handled.addEventListener("keydown", (e) => e.preventDefault());
    document.body.append(handled);
    handled.focus();
    press("ArrowDown", handled);
    handled.remove();

    expect(onNext).not.toHaveBeenCalled();
  });

  it("respects the boundary: no move when there is no next item", () => {
    const onNext = vi.fn();
    renderHook(() =>
      useKeyboardShortcuts({ onNext, onPrev: vi.fn(), canNext: false }),
    );
    press("ArrowDown");
    expect(onNext).not.toHaveBeenCalled();
  });
});
