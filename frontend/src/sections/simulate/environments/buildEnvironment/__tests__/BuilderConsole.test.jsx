import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { act } from "@testing-library/react";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";

import BuilderConsole from "../console/BuilderConsole";
import { CONSOLE_COPY } from "../build.constants";
import { injectComposerScaffold } from "../console/composerScaffoldBus";
import { getBuilderMode, setBuilderMode } from "../console/builderModeBus";

beforeAll(() => {
  // jsdom has no layout; the console auto-scrolls to the latest turn.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  // The builder mode is module-level; reset so no test leaks a non-default
  // mode into another.
  setBuilderMode("auto");
});

const builderTurn = {
  id: "t1",
  role: "builder",
  title: "Understanding the agent",
  steps: [
    { kind: "think", text: "Let me read the repository" },
    { kind: "tool", label: "list_files", result: "42 files scanned" },
    { kind: "file", path: "contract.json", note: "wrote the contract" },
    { kind: "json", label: "issue_refund", value: "seeded 99 rows" },
    { kind: "note", text: "a plain note" },
  ],
};

const userTurn = { id: "u1", role: "user", text: "make it harder" };

describe("BuilderConsole", () => {
  it("shows the idle header and empty state when there are no turns", () => {
    render(<BuilderConsole turns={[]} running={false} />);
    expect(screen.getByText(CONSOLE_COPY.idle)).toBeInTheDocument();
    expect(screen.getByText(CONSOLE_COPY.empty)).toBeInTheDocument();
  });

  it("shows the working header and a disabled Stop with the orbit during a send", () => {
    render(<BuilderConsole turns={[]} running onStop={vi.fn()} />);
    expect(screen.getByText(CONSOLE_COPY.working)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeDisabled();
    expect(screen.getByTestId("builder-console-stop-orbit")).toBeInTheDocument();
  });

  it("shows the working cue while a turn is in flight (canStop) but keeps the composer usable", () => {
    render(
      <BuilderConsole
        turns={[{ id: "u1", role: "user", text: "hey" }]}
        running={false}
        canStop
        onStop={vi.fn()}
      />,
    );
    // waiting-for-reply indicator is visible even though the POST already returned
    expect(screen.getByTestId("builder-console-stop-orbit")).toBeInTheDocument();
    expect(screen.getByText(CONSOLE_COPY.working)).toBeInTheDocument();
    // ...and the user can still type an interjection (not blocked)
    expect(screen.getByPlaceholderText(CONSOLE_COPY.placeholder)).not.toBeDisabled();
  });

  it("offers Stop without the working cue for work that only waits", () => {
    render(
      <BuilderConsole
        turns={[{ id: "u1", role: "user", text: "hey" }]}
        running={false}
        canStop
        working={false}
        onStop={vi.fn()}
      />,
    );
    expect(screen.getByText(CONSOLE_COPY.idle)).toBeInTheDocument();
    expect(screen.queryByTestId("builder-console-stop-orbit")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
  });

  it("swaps Stop for Send while the user has typed something", () => {
    const onSend = vi.fn();
    render(<BuilderConsole turns={[userTurn]} running={false} canStop onStop={vi.fn()} onSend={onSend} />);
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();

    const input = screen.getByPlaceholderText(CONSOLE_COPY.placeholder);
    fireEvent.change(input, { target: { value: "try again" } });
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledWith("try again");

    // Sending clears the draft, so Stop is back while the turn is still in flight.
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
  });

  it("disables Stop while a Stop is on its way", () => {
    render(
      <BuilderConsole
        turns={[{ id: "u1", role: "user", text: "hey" }]}
        running={false}
        canStop
        stopping
        onStop={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Stop" })).toBeDisabled();
    expect(screen.getByText(CONSOLE_COPY.stopping)).toBeInTheDocument();
  });

  describe("scrolling", () => {
    const toolTurn = (steps) => ({
      id: "t1",
      role: "builder",
      steps: Array.from({ length: steps }, (_, i) => ({ id: `s${i}`, kind: "tool", label: "read" })),
    });
    const withNote = (turn) => ({ ...turn, steps: [...turn.steps, { id: "n1", kind: "note", text: "Done" }] });
    const jump = (name) => screen.queryByRole("button", { name });
    let scrollTo;
    let original;

    // jsdom has no layout, so give the list one and fire the event a browser would.
    const scrollList = (scrollTop, scrollHeight = 2000) => {
      const list = screen.getByTestId("builder-console-list");
      Object.defineProperties(list, {
        scrollHeight: { configurable: true, value: scrollHeight },
        clientHeight: { configurable: true, value: 500 },
        scrollTop: { configurable: true, writable: true, value: scrollTop },
      });
      fireEvent.scroll(list);
    };
    const renderScrolledUp = (props) => {
      const view = render(<BuilderConsole turns={[toolTurn(1)]} running={false} canStop {...props} />);
      scrollList(1500);
      scrollList(100);
      scrollTo.mockClear();
      return view;
    };

    beforeEach(() => {
      original = window.HTMLElement.prototype.scrollTo;
      scrollTo = vi.fn();
      window.HTMLElement.prototype.scrollTo = scrollTo;
    });
    afterEach(() => {
      window.HTMLElement.prototype.scrollTo = original;
    });

    it("follows new events at the bottom", () => {
      const { rerender } = render(<BuilderConsole turns={[toolTurn(1)]} running={false} canStop />);
      scrollList(1500);
      scrollTo.mockClear();
      rerender(<BuilderConsole turns={[toolTurn(2)]} running={false} canStop />);
      expect(scrollTo).toHaveBeenCalledTimes(1);
      expect(jump(CONSOLE_COPY.jumpWorking)).not.toBeInTheDocument();
    });

    it("keeps following when a big event leaves the list far from the bottom", () => {
      const { rerender } = render(<BuilderConsole turns={[toolTurn(1)]} running={false} canStop />);
      scrollList(100);
      scrollList(400);
      scrollTo.mockClear();
      rerender(<BuilderConsole turns={[toolTurn(2)]} running={false} canStop />);
      expect(scrollTo).toHaveBeenCalledTimes(1);
    });

    it("stops following after a small scroll up, while reading the last rows", () => {
      const { rerender } = render(<BuilderConsole turns={[toolTurn(1)]} running={false} canStop />);
      scrollList(1500);
      scrollList(1440);
      scrollTo.mockClear();
      rerender(<BuilderConsole turns={[toolTurn(2)]} running={false} canStop />);
      expect(scrollTo).not.toHaveBeenCalled();
    });

    it("keeps following when text rewraps and moves the list", () => {
      const { rerender } = render(<BuilderConsole turns={[toolTurn(1)]} running={false} canStop />);
      scrollList(1500);
      scrollList(1200, 2600);
      scrollTo.mockClear();
      rerender(<BuilderConsole turns={[toolTurn(2)]} running={false} canStop />);
      expect(scrollTo).toHaveBeenCalledTimes(1);
    });

    it("stays put when scrolled up, for new events and for Stop appearing or going", () => {
      const { rerender } = renderScrolledUp();
      rerender(<BuilderConsole turns={[toolTurn(2)]} running={false} canStop />);
      rerender(<BuilderConsole turns={[toolTurn(2)]} running={false} canStop={false} />);
      expect(scrollTo).not.toHaveBeenCalled();
    });

    it("shows the ship for tool rows, then the ping for a message", () => {
      const { rerender } = renderScrolledUp();
      rerender(<BuilderConsole turns={[toolTurn(2)]} running={false} canStop />);
      expect(jump(CONSOLE_COPY.jumpWorking)).toBeInTheDocument();

      rerender(<BuilderConsole turns={[withNote(toolTurn(2))]} running={false} canStop />);
      expect(jump(CONSOLE_COPY.jumpWorking)).not.toBeInTheDocument();
      expect(jump(CONSOLE_COPY.jumpNewMessage)).toBeInTheDocument();
      // Screen readers hear it too, not only the changed button label.
      expect(screen.getByText(CONSOLE_COPY.newMessage, { selector: '[role="status"]' })).toBeInTheDocument();
    });

    it("pings for a system card, which has no steps, even when idle", () => {
      const { rerender } = renderScrolledUp({ canStop: false });
      const card = { id: "r1", role: "system", kind: "reapply", text: "The edits that went live with v2 were rolled back." };
      rerender(<BuilderConsole turns={[toolTurn(1), card]} running={false} canStop={false} />);
      expect(jump(CONSOLE_COPY.jumpNewMessage)).toBeInTheDocument();
    });

    it("shows no button when scrolled up with nothing happening", () => {
      renderScrolledUp({ canStop: false });
      expect(jump(CONSOLE_COPY.jumpWorking)).not.toBeInTheDocument();
    });

    it("jumps down and resumes following from the button", () => {
      const { rerender } = renderScrolledUp();
      rerender(<BuilderConsole turns={[withNote(toolTurn(1))]} running={false} canStop />);
      fireEvent.click(jump(CONSOLE_COPY.jumpNewMessage));
      expect(scrollTo).toHaveBeenCalledTimes(1);
      expect(jump(CONSOLE_COPY.jumpNewMessage)).not.toBeInTheDocument();

      rerender(<BuilderConsole turns={[withNote(toolTurn(2))]} running={false} canStop />);
      expect(scrollTo).toHaveBeenCalledTimes(2);
    });

    it("clears the new message once the user scrolls back down", () => {
      const { rerender } = renderScrolledUp();
      rerender(<BuilderConsole turns={[withNote(toolTurn(1))]} running={false} canStop />);
      scrollList(1500);
      expect(jump(CONSOLE_COPY.jumpNewMessage)).not.toBeInTheDocument();
    });

    it("returns to the latest turn on a send", () => {
      const { rerender } = renderScrolledUp();
      rerender(<BuilderConsole turns={[toolTurn(1)]} running canStop />);
      expect(scrollTo).toHaveBeenCalled();
    });
  });

  it("shows no collapse button unless onCollapse is given", () => {
    render(<BuilderConsole turns={[]} running={false} />);
    expect(screen.queryByRole("button", { name: CONSOLE_COPY.collapse })).not.toBeInTheDocument();
  });

  it("calls onCollapse from the header button", () => {
    const onCollapse = vi.fn();
    render(<BuilderConsole turns={[]} running={false} onCollapse={onCollapse} />);
    fireEvent.click(screen.getByRole("button", { name: CONSOLE_COPY.collapse }));
    expect(onCollapse).toHaveBeenCalledTimes(1);
  });

  it("shows the collapse tooltip on hover only while the console is open", async () => {
    const { rerender } = render(<BuilderConsole turns={[]} running={false} onCollapse={vi.fn()} />);
    fireEvent.mouseOver(screen.getByRole("button", { name: CONSOLE_COPY.collapse }));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(CONSOLE_COPY.collapse);

    // Collapsed (hidden): the tooltip must not linger over the right pane.
    rerender(<BuilderConsole turns={[]} running={false} onCollapse={vi.fn()} active={false} />);
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
  });

  it("scrolls only its own list to the latest turn on reopen, and not while hidden", () => {
    const scrollTo = vi.fn();
    const original = window.HTMLElement.prototype.scrollTo;
    window.HTMLElement.prototype.scrollTo = scrollTo;
    const scrollIntoView = window.HTMLElement.prototype.scrollIntoView;
    try {
      const { rerender } = render(<BuilderConsole turns={[userTurn]} running={false} active={false} />);
      scrollTo.mockClear();
      scrollIntoView.mockClear();

      rerender(<BuilderConsole turns={[userTurn, builderTurn]} running={false} active={false} />);
      expect(scrollTo).not.toHaveBeenCalled();

      // jsdom reports scrollHeight 0; give the list a real height so the
      // assertion pins the target, not just the behaviour.
      Object.defineProperty(screen.getByTestId("builder-console-list"), "scrollHeight", {
        configurable: true,
        value: 4321,
      });
      rerender(<BuilderConsole turns={[userTurn, builderTurn]} running={false} active />);
      expect(scrollTo).toHaveBeenCalledWith({ top: 4321, behavior: "smooth" });
      // scrollIntoView would also scroll the ancestors (the collapsing column).
      expect(scrollIntoView).not.toHaveBeenCalled();
    } finally {
      window.HTMLElement.prototype.scrollTo = original;
    }
  });

  it("renders a builder turn: title, prose, tool, file and expandable json", () => {
    render(<BuilderConsole turns={[builderTurn]} running={false} />);

    const title = screen.getByText("Understanding the agent");
    expect(title).toBeInTheDocument();
    expect(title).not.toHaveStyle({ textTransform: "uppercase" });

    expect(screen.getByText("Let me read the repository")).toBeInTheDocument();
    expect(screen.getByText("a plain note")).toBeInTheDocument();

    expect(screen.getByText("list_files")).toBeInTheDocument();
    expect(screen.getByText("42 files scanned")).toBeInTheDocument();

    expect(screen.getByText("contract.json")).toBeInTheDocument();
    expect(screen.getByText("wrote the contract")).toBeInTheDocument();

    // json row starts collapsed; its value only shows after a click.
    expect(screen.getByText("issue_refund")).toBeInTheDocument();
    expect(screen.queryByText("seeded 99 rows")).toBeNull();
    fireEvent.click(screen.getByText("issue_refund"));
    expect(screen.getByText("seeded 99 rows")).toBeInTheDocument();
  });

  it("renders a user turn in the bubble", () => {
    render(<BuilderConsole turns={[userTurn]} running={false} />);
    expect(screen.getByText("make it harder")).toBeInTheDocument();
  });

  describe("long content stays inside the chat column", () => {
    const LONG_URL =
      "https://dev.app.futureagi.com/dashboard/simulate/environments/bece5ca5-a985-4583-8953-764333aab3eb/runs/c8e3b646-de9c-4a5c-bcab-2dfea9856e45?tab=scenarios&filter=failed";

    it("breaks long unbroken tokens anywhere in the message list", () => {
      render(<BuilderConsole turns={[{ id: "u1", role: "user", text: LONG_URL }]} running={false} />);
      expect(window.getComputedStyle(screen.getByTestId("builder-console-list")).overflowWrap).toBe("anywhere");
    });

    it("keeps the line breaks the user typed", () => {
      render(<BuilderConsole turns={[{ id: "u1", role: "user", text: "first line\nsecond line" }]} running={false} />);
      const bubble = screen.getByText(/first line/);
      expect(bubble.textContent).toBe("first line\nsecond line");
      expect(window.getComputedStyle(bubble).whiteSpace).toBe("pre-wrap");
    });

    it("scrolls a builder code block inside its own box", () => {
      const turn = {
        id: "b1",
        role: "builder",
        steps: [
          {
            id: "s1",
            kind: "note",
            markdown: true,
            text: '```\n{"guest_booking_pin_policy":{"target_phone_number":"+19258565786","pin":"7682"}}\n```',
          },
        ],
      };
      const { container } = render(<BuilderConsole turns={[turn]} running={false} />);
      const pre = container.querySelector("pre");
      expect(pre).not.toBeNull();
      expect(window.getComputedStyle(pre).overflowX).toBe("auto");
    });
  });

  it("shows no suggestion chips and no builder-mode picker", () => {
    render(<BuilderConsole turns={[]} running={false} onSend={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Auto/ })).toBeNull();
    expect(screen.queryByText("Summarise what's in this environment")).toBeNull();
    // The chat still sends the default mode from the bus.
    expect(getBuilderMode()).toBe("auto");
  });

  it("sends the draft on Enter and clears the field", () => {
    const onSend = vi.fn();
    render(<BuilderConsole turns={[]} running={false} onSend={onSend} />);
    const field = screen.getByPlaceholderText(CONSOLE_COPY.placeholder);
    fireEvent.change(field, { target: { value: "hello" } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hello");
    expect(field).toHaveValue("");
  });

  it("does not send on Shift+Enter", () => {
    const onSend = vi.fn();
    render(<BuilderConsole turns={[]} running={false} onSend={onSend} />);
    const field = screen.getByPlaceholderText(CONSOLE_COPY.placeholder);
    fireEvent.change(field, { target: { value: "world" } });
    fireEvent.keyDown(field, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("disables the send button on an empty draft", () => {
    render(<BuilderConsole turns={[]} running={false} onSend={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("freezes the composer while building: reason shown, input and send disabled", () => {
    const onSend = vi.fn();
    render(
      <BuilderConsole turns={[]} running={false} onSend={onSend} frozen frozenReason="Still building" />,
    );

    // The reason replaces the idle header and the placeholder.
    expect(screen.getAllByText("Still building").length).toBeGreaterThanOrEqual(1);
    const field = screen.getByPlaceholderText("Still building");
    expect(field).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();

    // A blocked send never reaches the caller.
    fireEvent.keyDown(field, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("hides suggestion chips while frozen", () => {
    render(
      <BuilderConsole turns={[]} running={false} chips={["Show tools"]} frozen frozenReason="x" />,
    );
    expect(screen.queryByRole("button", { name: "Show tools" })).toBeNull();
  });

  it("pins an injected scaffold as a removable chip and prepends it on send", () => {
    const onSend = vi.fn();
    render(<BuilderConsole turns={[]} running={false} onSend={onSend} />);

    act(() => injectComposerScaffold("Rework the rushed-caller persona"));
    expect(screen.getByText("Rework the rushed-caller persona")).toBeInTheDocument();

    // The scaffold alone enables send; its text leads the outgoing message.
    const field = screen.getByPlaceholderText(CONSOLE_COPY.placeholder);
    fireEvent.change(field, { target: { value: "make it harder" } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("Rework the rushed-caller persona. make it harder");

    // The pins clear after send.
    expect(screen.queryByText("Rework the rushed-caller persona")).toBeNull();
  });

  it("removes a pinned scaffold with its × button", () => {
    render(<BuilderConsole turns={[]} running={false} onSend={vi.fn()} />);
    act(() => injectComposerScaffold("Add a dispute case"));
    fireEvent.click(screen.getByRole("button", { name: "Remove Add a dispute case" }));
    expect(screen.queryByText("Add a dispute case")).toBeNull();
  });

  it("renders an ask step inline as the AskUserQuestion card", () => {
    const askTurn = {
      id: "q1",
      role: "builder",
      steps: [
        { kind: "note", text: "Before I apply that, one decision:" },
        {
          kind: "ask",
          question: {
            step: 1, total: 1, prompt: "How strict should the refund rule be?",
            multiSelect: false, options: [{ label: "Escalate every refund" }],
          },
          onSubmit: vi.fn(),
          onSkip: vi.fn(),
        },
      ],
    };
    render(<BuilderConsole turns={[askTurn]} running={false} />);
    expect(screen.getByText("How strict should the refund rule be?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit" })).toBeInTheDocument();
  });
});
