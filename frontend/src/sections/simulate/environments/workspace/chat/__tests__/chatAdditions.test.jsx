import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import BuilderConsole from "../../../buildEnvironment/console/BuilderConsole";
import ChatSplitPane from "../../../components/ChatSplitPane";
import SelectionContextChip from "../SelectionContextChip";
import { CHAT_SUGGESTIONS } from "../chatSuggestions";
import { CHAT_PANE_STORAGE_KEYS } from "../../../components/chatSplitPane.constants";

describe("chat suggestions", () => {
  it("has Vel's chips for each tab, without em dashes", () => {
    expect(CHAT_SUGGESTIONS.overview).toEqual([
      "Summarise what's in this environment",
      "What's still missing before we can run?",
      "Explain the tools and rules to me",
    ]);
    expect(CHAT_SUGGESTIONS.runs).toEqual([
      "Summarise the last run",
      "Which scenarios fail most often?",
    ]);
    Object.values(CHAT_SUGGESTIONS)
      .flat()
      .forEach((c) => expect(c).not.toMatch(/—/));
  });

  it("shows the chips and sends one to the chat when clicked", () => {
    const onSend = vi.fn();
    render(
      <BuilderConsole
        turns={[]}
        onSend={onSend}
        chips={CHAT_SUGGESTIONS.overview}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Explain the tools and rules to me" }),
    );
    expect(onSend).toHaveBeenCalledWith("Explain the tools and rules to me");
  });

  it("says what the empty chat can do", () => {
    render(
      <BuilderConsole
        turns={[]}
        onSend={vi.fn()}
        emptyText="Refund line is ready. Ask me to add scenarios."
      />,
    );
    expect(
      screen.getByText("Refund line is ready. Ask me to add scenarios."),
    ).toBeInTheDocument();
  });

  it("hides the chips while the chat is frozen", () => {
    render(
      <BuilderConsole
        turns={[]}
        onSend={vi.fn()}
        chips={CHAT_SUGGESTIONS.overview}
        frozen
        frozenReason="Still building"
      />,
    );
    expect(
      screen.queryByRole("button", {
        name: "Explain the tools and rules to me",
      }),
    ).toBeNull();
  });
});

describe("SelectionContextChip", () => {
  it("names the scenarios being edited and clears them", () => {
    const onClear = vi.fn();
    render(
      <SelectionContextChip
        count={3}
        rows={[
          { name: "refund_over_limit" },
          { name: "late_parcel" },
          { name: "x" },
        ]}
        onClear={onClear}
      />,
    );
    expect(
      screen.getByText(
        (_, el) =>
          el?.tagName === "P" &&
          el.textContent ===
            "Editing 3 scenarios: refund_over_limit, late_parcel +1",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(onClear).toHaveBeenCalled();
  });

  it("names an all-matching selection by its count", () => {
    render(<SelectionContextChip count={60} rows={[]} all onClear={vi.fn()} />);
    expect(
      screen.getByText(
        (_, el) =>
          el?.tagName === "P" &&
          el.textContent === "Editing 60 scenarios: all matching",
      ),
    ).toBeInTheDocument();
  });
});

describe("ChatSplitPane while folded", () => {
  beforeEach(() =>
    window.localStorage.setItem(CHAT_PANE_STORAGE_KEYS.collapsed, "true"),
  );

  it("counts replies that arrive while the chat is folded", () => {
    const chat = () => <div>chat</div>;
    const { rerender } = render(
      <ChatSplitPane chat={chat} replies={2}>
        <div>body</div>
      </ChatSplitPane>,
    );
    expect(screen.queryByLabelText(/new from the builder/)).toBeNull();
    rerender(
      <ChatSplitPane chat={chat} replies={3}>
        <div>body</div>
      </ChatSplitPane>,
    );
    expect(screen.getByLabelText("1 new from the builder")).toBeInTheDocument();
  });
});
