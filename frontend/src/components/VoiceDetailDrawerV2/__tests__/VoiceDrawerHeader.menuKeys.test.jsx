import { describe, it, expect, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  within,
} from "src/utils/test-utils";
import VoiceDrawerHeader from "../VoiceDrawerHeader";
import VoiceActionsDropdown from "../VoiceActionsDropdown";

// The drawer header listens for ArrowUp/ArrowDown on document to move to the
// previous/next call. Arrows pressed inside the Actions menu belong to the
// menu: moving the drawer there swaps the call under the still-open menu, and
// Enter then runs the action on a different call.
const renderHeaderWithMenu = () => {
  const onNext = vi.fn();
  const onPrev = vi.fn();
  const onAction = vi.fn();
  render(
    <>
      <VoiceDrawerHeader
        callId="call-1"
        onClose={vi.fn()}
        onPrev={onPrev}
        onNext={onNext}
        hasPrev
        hasNext
      />
      <VoiceActionsDropdown onAction={onAction} />
    </>,
  );
  return { onNext, onPrev, onAction };
};

const openActionsMenu = async () => {
  await userEvent.click(screen.getByRole("button", { name: /actions/i }));
  return screen.getByRole("menu");
};

describe("VoiceDrawerHeader arrows inside the Actions menu", () => {
  it("ArrowDown on a menu item does not move to the next call", async () => {
    const { onNext } = renderHeaderWithMenu();
    const menu = await openActionsMenu();

    fireEvent.keyDown(
      within(menu).getByRole("menuitem", { name: /^annotate$/i }),
      { key: "ArrowDown" },
    );

    expect(onNext).not.toHaveBeenCalled();
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("ArrowUp on a menu item does not move to the previous call", async () => {
    const { onPrev } = renderHeaderWithMenu();
    const menu = await openActionsMenu();

    fireEvent.keyDown(
      within(menu).getByRole("menuitem", { name: /move to dataset/i }),
      { key: "ArrowUp" },
    );

    expect(onPrev).not.toHaveBeenCalled();
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("Alt+ArrowDown on a menu item does not move to the next call", async () => {
    const { onNext } = renderHeaderWithMenu();
    const menu = await openActionsMenu();

    fireEvent.keyDown(
      within(menu).getByRole("menuitem", { name: /^annotate$/i }),
      { key: "ArrowDown", altKey: true },
    );

    expect(onNext).not.toHaveBeenCalled();
  });

  it("ArrowDown on the closed Actions trigger still moves to the next call", () => {
    const { onNext } = renderHeaderWithMenu();

    fireEvent.keyDown(screen.getByRole("button", { name: /actions/i }), {
      key: "ArrowDown",
    });

    expect(onNext).toHaveBeenCalledTimes(1);
  });
});

describe("VoiceDrawerHeader arrows a focused widget already handled", () => {
  it("ignores ArrowUp/ArrowDown whose default was prevented", () => {
    const onNext = vi.fn();
    const onPrev = vi.fn();
    render(
      <>
        <VoiceDrawerHeader
          callId="call-1"
          onClose={vi.fn()}
          onPrev={onPrev}
          onNext={onNext}
          hasPrev
          hasNext
        />
        <button type="button" onKeyDown={(e) => e.preventDefault()}>
          widget
        </button>
      </>,
    );
    const widget = screen.getByRole("button", { name: "widget" });

    fireEvent.keyDown(widget, { key: "ArrowDown" });
    fireEvent.keyDown(widget, { key: "ArrowUp" });

    expect(onNext).not.toHaveBeenCalled();
    expect(onPrev).not.toHaveBeenCalled();
  });
});
