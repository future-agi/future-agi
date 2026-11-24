import { useEffect } from "react";
import { describe, it, expect, beforeAll, beforeEach, vi } from "vitest";
import { act, render, screen, fireEvent } from "src/utils/test-utils";

import ChatSplitPane from "../ChatSplitPane";
import BuilderConsole from "../../buildEnvironment/console/BuilderConsole";
import { focusBuilderChat } from "../../buildEnvironment/console/builderChatFocusBus";
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

  it.each([
    ["a message of the person's", "u4"],
    ["an answer to the builder's question", "answer-1"],
  ])("opens at %s when asked, not at the newest", (_, messageId) => {
    const turns = [
      ...makeTurns(TURN_COUNT),
      {
        id: "b-ask",
        role: "builder",
        steps: [
          {
            id: "ask-1",
            kind: "ask",
            question: { text: "Change it?", options: [] },
            resolved: true,
            answerText: "Yes",
            answerId: "answer-1",
          },
        ],
      },
    ];
    render(renderSplit(turns));
    collapse();
    const list = screen.getByTestId("builder-console-list");
    Object.defineProperty(list, "scrollHeight", {
      configurable: true,
      value: 9876,
    });
    Object.defineProperty(
      list.querySelector(
        `[data-message-id="${messageId}"], [data-answer-id="${messageId}"]`,
      ),
      "offsetTop",
      { configurable: true, value: 321 },
    );
    const scrollTo = window.HTMLElement.prototype.scrollTo;
    scrollTo.mockClear();
    act(() => focusBuilderChat(messageId));
    expect(chatPane()).toBeVisible();
    expect(scrollTo.mock.calls).toEqual([[{ top: 321, behavior: "smooth" }]]);
  });

  it.each([undefined, "not-loaded"])(
    "opens at the newest when there is no message to show (%s)",
    (messageId) => {
      render(renderSplit(makeTurns(TURN_COUNT)));
      collapse();
      const scrollTo = window.HTMLElement.prototype.scrollTo;
      scrollTo.mockClear();
      act(() => focusBuilderChat(messageId));
      expect(chatPane()).toBeVisible();
      expect(scrollTo).toHaveBeenCalled();
    },
  );

  it("keeps the reader's place when older steps load above it", () => {
    const start = (oldest) => ({
      id: "older-steps",
      role: "history",
      oldest,
      hasOlder: true,
    });
    const older = {
      id: "b-old",
      role: "builder",
      steps: [{ id: "s-old", kind: "error", text: "older error" }],
    };
    const { rerender } = render(renderSplit([start(201), ...makeTurns(4)]));
    const list = screen.getByTestId("builder-console-list");
    list.scrollTop = 40;
    // The older step adds 300px above everything else.
    const rect = vi
      .spyOn(window.HTMLElement.prototype, "getBoundingClientRect")
      .mockImplementation(() => ({
        top: screen.queryByText("older error")
          ? 300 - (list.scrollTop - 40)
          : 0,
        bottom: 500,
      }));
    rerender(renderSplit([start(1), older, ...makeTurns(4)]));
    rect.mockRestore();
    expect(list.scrollTop).toBe(340);
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

    // jsdom reports scrollHeight 0; give the list a real height so the
    // assertion pins where it scrolls, not just how.
    Object.defineProperty(
      screen.getByTestId("builder-console-list"),
      "scrollHeight",
      { configurable: true, value: 9876 },
    );
    expand();

    expect(scrollTo).toHaveBeenCalledWith({ top: 9876, behavior: "smooth" });
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
