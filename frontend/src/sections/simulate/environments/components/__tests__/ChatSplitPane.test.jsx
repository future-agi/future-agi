import { useState } from "react";
import PropTypes from "prop-types";
import {
  describe,
  it,
  expect,
  beforeAll,
  beforeEach,
  afterEach,
  vi,
} from "vitest";
import { act } from "@testing-library/react";
import { render, screen, fireEvent } from "src/utils/test-utils";

import ChatSplitPane from "../ChatSplitPane";
import {
  CHAT_PANE_DEFAULT_WIDTH,
  CHAT_PANE_MIN_WIDTH,
  CHAT_PANE_MAX_WIDTH,
  CHAT_PANE_STORAGE_KEYS,
} from "../chatSplitPane.constants";

// A stand-in chat with its own local state, so the tests can prove collapsing
// hides the chat without unmounting it (an unmount would drop the draft).
function FakeChat({ onCollapse, collapseRef }) {
  const [draft, setDraft] = useState("");
  return (
    <div>
      <input
        aria-label="draft"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <button type="button" ref={collapseRef} onClick={onCollapse}>
        Collapse chat
      </button>
    </div>
  );
}

FakeChat.propTypes = {
  onCollapse: PropTypes.func,
  collapseRef: PropTypes.object,
};

const renderSplit = (props = {}) =>
  render(
    <ChatSplitPane
      chat={({ collapse, collapseRef }) => (
        <FakeChat onCollapse={collapse} collapseRef={collapseRef} />
      )}
      {...props}
    >
      <div>right pane</div>
    </ChatSplitPane>,
  );

const chatPane = () => screen.getByTestId("chat-split-chat");

// jsdom has no PointerEvent; a MouseEvent subclass carries clientX and button,
// which is all the separator reads.
beforeAll(() => {
  if (!window.PointerEvent) {
    window.PointerEvent = class PointerEvent extends MouseEvent {
      constructor(type, init = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 1;
      }
    };
  }
});
const separator = () => screen.getByRole("separator", { name: "Resize chat" });

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ChatSplitPane", () => {
  it("renders the chat, the separator and the right pane at the default width", () => {
    renderSplit();
    expect(screen.getByText("right pane")).toBeInTheDocument();
    expect(chatPane()).toBeVisible();
    expect(chatPane()).toHaveStyle({ width: `${CHAT_PANE_DEFAULT_WIDTH}px` });
    expect(separator()).toHaveAttribute(
      "aria-valuenow",
      String(CHAT_PANE_DEFAULT_WIDTH),
    );
    expect(
      screen.queryByRole("button", { name: "Open chat" }),
    ).not.toBeInTheDocument();
  });

  it("collapses to the rail without unmounting the chat, and reopens it intact", () => {
    renderSplit();
    fireEvent.change(screen.getByLabelText("draft"), {
      target: { value: "half typed" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));

    // Still mounted, just hidden; the separator goes away with it.
    expect(chatPane()).toBeInTheDocument();
    expect(chatPane()).not.toBeVisible();
    expect(screen.queryByRole("separator")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));

    expect(chatPane()).toBeVisible();
    expect(screen.getByLabelText("draft")).toHaveValue("half typed");
    expect(
      screen.queryByRole("button", { name: "Open chat" }),
    ).not.toBeInTheDocument();
  });

  it("reopens from the rail's expand arrow too", () => {
    renderSplit();
    fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Expand chat" }));
    expect(chatPane()).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Expand chat" }),
    ).not.toBeInTheDocument();
  });

  it("passes the open state to the chat render prop", () => {
    const chat = vi.fn(({ collapse }) => <FakeChat onCollapse={collapse} />);
    render(
      <ChatSplitPane chat={chat}>
        <div>right pane</div>
      </ChatSplitPane>,
    );
    expect(chat).toHaveBeenLastCalledWith(
      expect.objectContaining({ open: true }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
    expect(chat).toHaveBeenLastCalledWith(
      expect.objectContaining({ open: false }),
    );
  });

  it("shows the busy dot on the rail only while busy", () => {
    const { rerender } = renderSplit({ busy: true });
    fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
    expect(screen.getByTestId("chat-split-busy")).toBeInTheDocument();

    rerender(
      <ChatSplitPane
        chat={({ collapse }) => <FakeChat onCollapse={collapse} />}
        busy={false}
      >
        <div>right pane</div>
      </ChatSplitPane>,
    );
    expect(screen.queryByTestId("chat-split-busy")).not.toBeInTheDocument();
  });

  it("resizes with the arrow keys, clamped to the min and max", () => {
    renderSplit();
    fireEvent.keyDown(separator(), { key: "ArrowRight" });
    expect(chatPane()).toHaveStyle({
      width: `${CHAT_PANE_DEFAULT_WIDTH + 12}px`,
    });

    fireEvent.keyDown(separator(), { key: "ArrowLeft", shiftKey: true });
    expect(chatPane()).toHaveStyle({
      width: `${CHAT_PANE_DEFAULT_WIDTH + 12 - 40}px`,
    });

    for (let i = 0; i < 20; i += 1)
      fireEvent.keyDown(separator(), { key: "ArrowLeft", shiftKey: true });
    expect(chatPane()).toHaveStyle({ width: `${CHAT_PANE_MIN_WIDTH}px` });

    for (let i = 0; i < 20; i += 1)
      fireEvent.keyDown(separator(), { key: "ArrowRight", shiftKey: true });
    expect(chatPane()).toHaveStyle({ width: `${CHAT_PANE_MAX_WIDTH}px` });
  });

  it("resets to the default width on double-click", () => {
    renderSplit();
    fireEvent.keyDown(separator(), { key: "ArrowRight", shiftKey: true });
    fireEvent.doubleClick(separator());
    expect(chatPane()).toHaveStyle({ width: `${CHAT_PANE_DEFAULT_WIDTH}px` });
  });

  it("remembers the width and the collapsed state", () => {
    const { unmount } = renderSplit();
    fireEvent.keyDown(separator(), { key: "ArrowRight", shiftKey: true });
    fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
    expect(window.localStorage.getItem(CHAT_PANE_STORAGE_KEYS.width)).toBe(
      String(CHAT_PANE_DEFAULT_WIDTH + 40),
    );
    expect(window.localStorage.getItem(CHAT_PANE_STORAGE_KEYS.collapsed)).toBe(
      "true",
    );
    unmount();

    renderSplit();
    expect(chatPane()).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));
    expect(chatPane()).toHaveStyle({
      width: `${CHAT_PANE_DEFAULT_WIDTH + 40}px`,
    });
    expect(window.localStorage.getItem(CHAT_PANE_STORAGE_KEYS.collapsed)).toBe(
      "false",
    );
  });

  it("clamps a stored width that is out of range", () => {
    window.localStorage.setItem(CHAT_PANE_STORAGE_KEYS.width, "5000");
    renderSplit();
    expect(chatPane()).toHaveStyle({ width: `${CHAT_PANE_MAX_WIDTH}px` });
  });

  it("still renders when localStorage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    renderSplit();
    expect(chatPane()).toHaveStyle({ width: `${CHAT_PANE_DEFAULT_WIDTH}px` });
    fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
    expect(
      screen.getByRole("button", { name: "Open chat" }),
    ).toBeInTheDocument();
  });

  describe("dragging the separator", () => {
    it("resizes with the pointer and saves the width on release", () => {
      renderSplit();
      fireEvent.pointerDown(separator(), { button: 0, clientX: 500 });
      fireEvent.pointerMove(separator(), { clientX: 560 });
      expect(chatPane()).toHaveStyle({
        width: `${CHAT_PANE_DEFAULT_WIDTH + 60}px`,
      });
      expect(
        window.localStorage.getItem(CHAT_PANE_STORAGE_KEYS.width),
      ).toBeNull();

      fireEvent.pointerUp(separator(), { clientX: 560 });
      expect(window.localStorage.getItem(CHAT_PANE_STORAGE_KEYS.width)).toBe(
        String(CHAT_PANE_DEFAULT_WIDTH + 60),
      );

      // Released: hovering over the line no longer resizes.
      fireEvent.pointerMove(separator(), { clientX: 700 });
      expect(chatPane()).toHaveStyle({
        width: `${CHAT_PANE_DEFAULT_WIDTH + 60}px`,
      });
    });

    it("ignores a right-click, so a lost pointerup can't leave it stuck dragging", () => {
      renderSplit();
      fireEvent.pointerDown(separator(), { button: 2, clientX: 500 });
      fireEvent.pointerMove(separator(), { clientX: 600 });
      expect(chatPane()).toHaveStyle({ width: `${CHAT_PANE_DEFAULT_WIDTH}px` });
    });

    it("ends the drag when the browser cancels it or capture is lost", () => {
      renderSplit();
      fireEvent.pointerDown(separator(), { button: 0, clientX: 500 });
      fireEvent.pointerMove(separator(), { clientX: 520 });
      fireEvent.pointerCancel(separator());
      fireEvent.pointerMove(separator(), { clientX: 700 });
      expect(chatPane()).toHaveStyle({
        width: `${CHAT_PANE_DEFAULT_WIDTH + 20}px`,
      });

      fireEvent.pointerDown(separator(), { button: 0, clientX: 500 });
      fireEvent.lostPointerCapture(separator());
      fireEvent.pointerMove(separator(), { clientX: 700 });
      expect(chatPane()).toHaveStyle({
        width: `${CHAT_PANE_DEFAULT_WIDTH + 20}px`,
      });
    });

    it("drops a drag in progress when the chat is collapsed", () => {
      renderSplit();
      fireEvent.pointerDown(separator(), { button: 0, clientX: 500 });
      fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
      fireEvent.click(screen.getByRole("button", { name: "Expand chat" }));
      // A fresh separator; moving over it is a hover, not a drag.
      fireEvent.pointerMove(separator(), { clientX: 700 });
      expect(chatPane()).toHaveStyle({ width: `${CHAT_PANE_DEFAULT_WIDTH}px` });
    });

    it("does not re-render the chat on every pointer move", () => {
      const chat = vi.fn(({ collapse, collapseRef }) => (
        <FakeChat onCollapse={collapse} collapseRef={collapseRef} />
      ));
      render(
        <ChatSplitPane chat={chat}>
          <div>right pane</div>
        </ChatSplitPane>,
      );
      const before = chat.mock.calls.length;
      fireEvent.pointerDown(separator(), { button: 0, clientX: 500 });
      for (let x = 501; x <= 520; x += 1)
        fireEvent.pointerMove(separator(), { clientX: x });
      fireEvent.pointerUp(separator(), { clientX: 520 });
      expect(chatPane()).toHaveStyle({
        width: `${CHAT_PANE_DEFAULT_WIDTH + 20}px`,
      });
      expect(chat.mock.calls.length).toBe(before);
    });
  });

  describe("focus on toggle", () => {
    it("moves focus from the collapse button to the rail's expand arrow, and back", () => {
      renderSplit();
      const collapseButton = screen.getByRole("button", {
        name: "Collapse chat",
      });
      collapseButton.focus();
      fireEvent.click(collapseButton);
      expect(screen.getByRole("button", { name: "Expand chat" })).toHaveFocus();

      fireEvent.click(screen.getByRole("button", { name: "Expand chat" }));
      expect(
        screen.getByRole("button", { name: "Collapse chat" }),
      ).toHaveFocus();
    });

    it("leaves focus alone when it wasn't in the side being hidden", () => {
      render(
        <ChatSplitPane
          chat={({ collapse, collapseRef }) => (
            <FakeChat onCollapse={collapse} collapseRef={collapseRef} />
          )}
        >
          <input aria-label="right pane field" />
        </ChatSplitPane>,
      );
      const field = screen.getByLabelText("right pane field");
      field.focus();
      // Collapsed from elsewhere (e.g. a programmatic call): focus stays put.
      fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
      expect(field).toHaveFocus();
    });

    it("never marks a layer aria-hidden while it can still hold focus", () => {
      renderSplit();
      const collapseButton = screen.getByRole("button", {
        name: "Collapse chat",
      });
      collapseButton.focus();
      fireEvent.click(collapseButton);
      expect(chatPane()).not.toHaveAttribute("aria-hidden");
      // Hidden by visibility (after the fade), which also drops it from the
      // accessibility tree and the tab order.
      expect(chatPane()).not.toBeVisible();
    });
  });

  describe("container width", () => {
    // jsdom has no layout: stub the measured width and capture the
    // ResizeObserver callback so a window resize can be replayed.
    let containerWidth;
    let resizeCallbacks;
    let OriginalResizeObserver;

    beforeEach(() => {
      containerWidth = 900;
      resizeCallbacks = [];
      OriginalResizeObserver = globalThis.ResizeObserver;
      globalThis.ResizeObserver = class {
        constructor(cb) {
          resizeCallbacks.push(cb);
        }
        observe() {}
        disconnect() {}
      };
      vi.spyOn(
        HTMLElement.prototype,
        "getBoundingClientRect",
      ).mockImplementation(() => ({
        width: containerWidth,
        height: 600,
        top: 0,
        left: 0,
        right: containerWidth,
        bottom: 600,
      }));
    });

    afterEach(() => {
      globalThis.ResizeObserver = OriginalResizeObserver;
    });

    const resizeContainer = (width) => {
      containerWidth = width;
      act(() => {
        resizeCallbacks.forEach((cb) => cb([{ contentRect: { width } }]));
      });
    };

    it("keeps the right pane at least 480px wide", () => {
      renderSplit();
      for (let i = 0; i < 20; i += 1) {
        fireEvent.keyDown(separator(), { key: "ArrowRight", shiftKey: true });
      }
      expect(chatPane()).toHaveStyle({ width: "420px" });
    });

    it("tells assistive tech the real maximum for this container", () => {
      renderSplit();
      expect(separator()).toHaveAttribute("aria-valuemax", "420");
      resizeContainer(1400);
      expect(separator()).toHaveAttribute(
        "aria-valuemax",
        String(CHAT_PANE_MAX_WIDTH),
      );
    });

    it("restores the chosen width when the window widens again", () => {
      containerWidth = 1400;
      renderSplit();
      for (let i = 0; i < 5; i += 1) {
        fireEvent.keyDown(separator(), { key: "ArrowRight", shiftKey: true });
      }
      expect(chatPane()).toHaveStyle({ width: "600px" });

      resizeContainer(1000);
      expect(chatPane()).toHaveStyle({ width: "520px" });

      resizeContainer(1400);
      expect(chatPane()).toHaveStyle({ width: "600px" });
      expect(window.localStorage.getItem(CHAT_PANE_STORAGE_KEYS.width)).toBe(
        "600",
      );
    });
  });
});
