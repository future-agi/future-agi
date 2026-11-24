import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  afterEach,
  describe,
  it,
  expect,
  beforeAll,
  beforeEach,
  vi,
} from "vitest";
import { act, render, screen, fireEvent } from "src/utils/test-utils";

const { enqueueSnackbar } = vi.hoisted(() => ({ enqueueSnackbar: vi.fn() }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar,
}));

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

  describe("keeps the reader's place when older ones load", () => {
    const start = (oldest) => ({
      id: "older-messages",
      role: "history",
      kind: "messages",
      oldest,
      hasOlder: true,
    });
    const step = (id) => ({
      id: `t-${id}`,
      role: "builder",
      steps: [{ id, kind: "error", text: id }],
    });
    // Every row is 100px, in the order drawn, in a pane 300px high.
    const layOut = (list) =>
      vi
        .spyOn(window.HTMLElement.prototype, "getBoundingClientRect")
        .mockImplementation(function rect() {
          if (this === list) return { top: 0, bottom: 300 };
          const rows = [...list.querySelectorAll("[data-message-id]")];
          const top = rows.indexOf(this) * 100 - list.scrollTop;
          return { top, bottom: top + 100 };
        });
    const land = (before, after, scrollTop, following) => {
      const { rerender } = render(renderSplit(before));
      const list = screen.getByTestId("builder-console-list");
      Object.defineProperty(list, "scrollHeight", {
        configurable: true,
        value: 9876,
      });
      const rect = layOut(list);
      // Scrolled up by hand, the list no longer follows the latest.
      if (!following) {
        fireEvent.scroll(list, { target: { scrollTop: 500 } });
        fireEvent.scroll(list, { target: { scrollTop } });
      }
      list.scrollTop = scrollTop;
      rerender(renderSplit(after));
      const at = list.scrollTop;
      rect.mockRestore();
      return at;
    };
    const held = ["a", "b", "c", "d"].map(step);

    it("by the first row in view, when they land above it", () => {
      const older = [step("x"), step("y")];
      expect(
        land([start(201), ...held], [start(1), ...older, ...held], 100),
      ).toBe(300);
    });

    it("by the first row in view, when they land below it", () => {
      const [a, b, ...rest] = held;
      expect(
        land(
          [a, b, start(201), ...rest],
          [a, start(1), b, step("x"), step("y"), ...rest],
          100,
        ),
      ).toBe(100);
    });

    it("at the latest, for a reader who follows it", () => {
      const [a, b, ...rest] = held;
      expect(
        land(
          [a, b, start(201), ...rest],
          [a, start(1), b, step("x"), step("y"), ...rest],
          100,
          true,
        ),
      ).toBe(300);
    });
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

describe("older messages", () => {
  const PAGE = 200;
  let observers = [];
  let original;

  // The marker is in view from the moment it is watched, until told otherwise.
  let markerInView = true;
  class FakeObserver {
    constructor(callback) {
      this.callback = callback;
    }
    observe() {
      observers.push(this);
    }
    disconnect() {
      observers = observers.filter((one) => one !== this);
    }
  }
  const settle = async () => {
    await act(async () => {
      [...observers].forEach((one) =>
        one.callback([{ isIntersecting: markerInView }]),
      );
    });
  };

  beforeEach(() => {
    observers = [];
    markerInView = true;
    original = globalThis.IntersectionObserver;
    globalThis.IntersectionObserver = FakeObserver;
    enqueueSnackbar.mockReset();
  });
  afterEach(() => {
    globalThis.IntersectionObserver = original;
  });

  // A chat with `pages` older pages, each answered when the test says so.
  function Chat({ pages, answers, found, running = false }) {
    const [held, setHeld] = useState({ pages: 0, loading: false });
    const onLoad = () => {
      setHeld((was) => ({ ...was, loading: true, failed: false }));
      return new Promise((resolve, reject) => {
        answers.push({
          ok: () => {
            setHeld((was) => ({ pages: was.pages + 1, loading: false }));
            resolve();
          },
          fail: () => {
            setHeld((was) => ({ ...was, loading: false, failed: true }));
            reject(new Error("offline"));
          },
        });
      });
    };
    const turns = [
      {
        id: "older-messages",
        role: "history",
        kind: "messages",
        oldest: (pages - held.pages) * PAGE + 1,
        hasOlder: held.pages < pages,
        loading: held.loading,
        failed: held.failed,
        onLoad,
      },
      ...(found && held.pages >= found
        ? [{ id: "wanted", role: "user", text: "the old message" }]
        : []),
      ...makeTurns(4),
    ];
    return (
      <ChatSplitPane
        chat={({ collapse, open }) => (
          <BuilderConsole
            turns={turns}
            running={running}
            onCollapse={collapse}
            active={open}
          />
        )}
      >
        <div>right pane</div>
      </ChatSplitPane>
    );
  }
  Chat.propTypes = {
    pages: PropTypes.number,
    answers: PropTypes.array,
    found: PropTypes.number,
    running: PropTypes.bool,
  };
  const open = (props) => {
    const answers = [];
    const view = render(<Chat answers={answers} {...props} />);
    return {
      answers,
      rerender: (more) =>
        view.rerender(<Chat answers={answers} {...props} {...more} />),
    };
  };
  const list = () => screen.getByTestId("builder-console-list");
  const scrollUp = () => fireEvent.wheel(list(), { deltaY: -100 });
  const answer = async (one, how = "ok") => {
    await act(async () => one[how]());
    await settle();
  };

  describe("load when the reader reaches where they start", () => {
    it("one page per reach, and the next only while the start is still in view", async () => {
      const { answers } = open({ pages: 4 });
      await settle();
      // Not on open, however short the chat is.
      expect(answers).toHaveLength(0);

      scrollUp();
      await settle();
      await settle();
      expect(answers).toHaveLength(1);

      // The page added too little to push the start out of view.
      await answer(answers[0]);
      expect(answers).toHaveLength(2);

      markerInView = false;
      await answer(answers[1]);
      expect(answers).toHaveLength(2);
    });

    it("stops after a failure until Try again", async () => {
      const { answers } = open({ pages: 4 });
      scrollUp();
      await settle();
      await answer(answers[0], "fail");
      scrollUp();
      await settle();
      expect(answers).toHaveLength(1);
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(answers).toHaveLength(2);
    });

    it("not when the chat scrolled itself there", async () => {
      const { answers } = open({ pages: 4 });
      scrollUp();
      act(() => focusBuilderChat("u0"));
      await settle();
      expect(answers).toHaveLength(0);
    });
  });

  describe("for a reader above where both start", () => {
    const start = (kind, over) => ({
      id: `older-${kind}`,
      role: "history",
      kind,
      oldest: 201,
      hasOlder: true,
      onLoad: vi.fn(() => Promise.resolve()),
      ...over,
    });
    const show = (events, messages) =>
      render(
        renderSplit([
          events,
          makeTurns(1)[0],
          messages,
          ...makeTurns(4).slice(1),
        ]),
      );

    it("stays in sight at the bottom, so a jump past it still reaches it", () => {
      show(start("events"), start("messages"));
      ["older-events", "older-messages"].forEach((id) =>
        expect(screen.getByTestId(id)).toHaveStyle({ position: "sticky" }),
      );
    });

    it.each([
      ["first the one that starts lower", {}, 0],
      ["then the other, once that one ends", { hasOlder: false }, 1],
      ["the other, when that one failed", { failed: true }, 1],
    ])("loads %s", async (_, over, events) => {
      const steps = start("events");
      const messages = start("messages", over);
      show(steps, messages);
      fireEvent.keyDown(list(), { key: "Home" });
      await settle();
      expect(steps.onLoad).toHaveBeenCalledTimes(events);
      expect(messages.onLoad).toHaveBeenCalledTimes(1 - events);
    });
  });

  describe("read to open the chat at a message that isn't loaded", () => {
    const looking = () => screen.queryByText("Looking for that message…");

    it("loads pages until it is there, then shows it", async () => {
      const { answers } = open({ pages: 9, found: 2 });
      act(() => focusBuilderChat("wanted"));
      expect(looking()).toBeInTheDocument();
      await answer(answers[0]);
      const { scrollTo } = window.HTMLElement.prototype;
      scrollTo.mockClear();
      await answer(answers[1]);
      expect(answers).toHaveLength(2);
      expect(scrollTo).toHaveBeenCalledTimes(1);
      expect(enqueueSnackbar).not.toHaveBeenCalled();
    });

    it.each([
      [
        "too far back",
        { pages: 9 },
        5,
        "That message is too far back to open here.",
      ],
      [
        "not in the chat",
        { pages: 2 },
        2,
        "That message isn't in this chat, so it can't be shown.",
      ],
    ])("gives up once when it is %s", async (_, props, asked, said) => {
      const { answers } = open(props);
      act(() => focusBuilderChat("wanted"));
      for (let page = 0; page < asked; page += 1) await answer(answers[page]);
      expect(answers).toHaveLength(asked);
      expect(enqueueSnackbar.mock.calls).toEqual([[said, { variant: "info" }]]);
    });

    it("gives up when a page fails, and looks again when asked again", async () => {
      const { answers } = open({ pages: 9 });
      act(() => focusBuilderChat("wanted"));
      await answer(answers[0], "fail");
      expect(answers).toHaveLength(1);
      expect(enqueueSnackbar.mock.calls).toEqual([
        ["Couldn't load the earlier messages. Try again.", { variant: "info" }],
      ]);
      act(() => focusBuilderChat("wanted"));
      expect(answers).toHaveLength(2);
    });

    it.each([
      ["scrolls by hand", () => fireEvent.wheel(list(), { deltaY: 100 })],
      ["sends a message", (rerender) => rerender({ running: true })],
    ])("stops looking when the reader %s", async (_, interrupt) => {
      const { answers, rerender } = open({ pages: 9 });
      act(() => focusBuilderChat("wanted"));
      interrupt(rerender);
      await answer(answers[0]);
      expect(answers).toHaveLength(1);
      expect(enqueueSnackbar).not.toHaveBeenCalled();
    });
  });
});
