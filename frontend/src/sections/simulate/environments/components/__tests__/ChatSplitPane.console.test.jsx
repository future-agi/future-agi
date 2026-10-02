import { useEffect } from "react";
import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import ChatSplitPane from "../ChatSplitPane";
import BuilderConsole from "../../buildEnvironment/console/BuilderConsole";
import { CONSOLE_COPY } from "../../buildEnvironment/build.constants";

// The split with the REAL builder console and a long conversation: collapsing
// must never unmount the console, and turns that arrive while it is collapsed
// must be there (and scrolled to) when it reopens.

const TURN_COUNT = 200;

const makeTurns = (count) =>
  Array.from({ length: count }, (_, i) =>
    i % 2 === 0
      ? { id: `u${i}`, role: "user", text: `user message ${i}` }
      : {
          id: `b${i}`,
          role: "builder",
          steps: [{ id: `s${i}`, kind: "note", text: `builder reply ${i}` }],
        },
  );

let mounts = 0;
let unmounts = 0;

// Counts real mounts/unmounts of the console subtree.
function ProbedConsole(props) {
  useEffect(() => {
    mounts += 1;
    return () => {
      unmounts += 1;
    };
  }, []);
  return <BuilderConsole {...props} />;
}

const renderSplit = (turns, busy = false) => (
  <ChatSplitPane
    busy={busy}
    chat={({ collapse, open }) => (
      <ProbedConsole
        turns={turns}
        running={false}
        onCollapse={collapse}
        active={open}
      />
    )}
  >
    <div>right pane</div>
  </ChatSplitPane>
);

const collapse = () =>
  fireEvent.click(screen.getByRole("button", { name: CONSOLE_COPY.collapse }));
const expand = () =>
  fireEvent.click(screen.getByRole("button", { name: "Open chat" }));
const chatPane = () => screen.getByTestId("chat-split-chat");

beforeAll(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  mounts = 0;
  unmounts = 0;
  window.localStorage.clear();
  window.HTMLElement.prototype.scrollTo = vi.fn();
});

describe("ChatSplitPane with a long builder conversation", () => {
  it("keeps the console mounted across repeated collapse and expand", () => {
    render(renderSplit(makeTurns(TURN_COUNT)));
    const firstNode = chatPane().firstElementChild;

    for (let i = 0; i < 5; i += 1) {
      collapse();
      expect(chatPane()).not.toBeVisible();
      expect(
        screen.getByText(`builder reply ${TURN_COUNT - 1}`),
      ).toBeInTheDocument();
      expect(screen.getByText("user message 0")).toBeInTheDocument();
      expand();
      expect(chatPane()).toBeVisible();
    }

    expect(mounts).toBe(1);
    expect(unmounts).toBe(0);
    // Same DOM node, not a re-created one.
    expect(chatPane().firstElementChild).toBe(firstNode);
  });

  it("keeps a half-typed draft through collapse with 200 turns on screen", () => {
    render(renderSplit(makeTurns(TURN_COUNT)));
    const composer = screen.getByPlaceholderText(CONSOLE_COPY.placeholder);
    fireEvent.change(composer, { target: { value: "half typed reply" } });

    collapse();
    expand();

    expect(screen.getByPlaceholderText(CONSOLE_COPY.placeholder)).toHaveValue(
      "half typed reply",
    );
    expect(mounts).toBe(1);
  });

  it("receives turns while collapsed and scrolls to the newest on reopen", () => {
    const { rerender } = render(renderSplit(makeTurns(TURN_COUNT), true));
    collapse();
    // The rail signals that work is still going on.
    expect(screen.getByTestId("chat-split-busy")).toBeInTheDocument();

    const scrollTo = window.HTMLElement.prototype.scrollTo;
    scrollTo.mockClear();

    // Three more turns stream in while the chat is hidden.
    for (let n = TURN_COUNT + 1; n <= TURN_COUNT + 3; n += 1) {
      rerender(renderSplit(makeTurns(n), true));
    }
    // Hidden: no scrolling while collapsed.
    expect(scrollTo).not.toHaveBeenCalled();
    expect(
      screen.getByText(`user message ${TURN_COUNT + 2}`),
    ).toBeInTheDocument();

    expand();

    expect(scrollTo).toHaveBeenCalledWith(
      expect.objectContaining({ behavior: "smooth" }),
    );
    expect(chatPane()).toBeVisible();
    expect(screen.getAllByText(/^user message \d+$/)).toHaveLength(
      (TURN_COUNT + 3 + 1) / 2,
    );
    expect(mounts).toBe(1);
    expect(unmounts).toBe(0);
    // scrollIntoView would scroll the collapsing column too; the console never uses it.
    expect(window.HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();
  });
});
