import React from "react";
import { describe, expect, it } from "vitest";
import { act, fireEvent, renderHook, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import {
  clampText,
  detectValueKind,
  isChatArray,
  parseJsonString,
  rawText,
} from "../valueKind";
import { diffLines, diffStats } from "../diffSegments";
import useValueViewMode, { VALUE_VIEW_MODE_KEY } from "../useValueViewMode";
import ValueViewer from "../ValueViewer";
import TextDiff from "../TextDiff";
import FieldCard from "../FieldCard";

describe("valueKind", () => {
  it("detects every kind", () => {
    expect(detectValueKind(null)).toBe("empty");
    expect(detectValueKind("")).toBe("empty");
    expect(detectValueKind([])).toBe("empty");
    expect(detectValueKind("Where is my parcel?")).toBe("text");
    expect(detectValueKind('{"a":1}')).toBe("json_string");
    expect(detectValueKind({ a: 1 })).toBe("json");
    expect(detectValueKind([{ role: "user", content: "hi" }])).toBe("chat");
    expect(detectValueKind('[{"role":"user","content":"hi"}]')).toBe("chat");
    expect(detectValueKind("https://x.io/a.png")).toBe("image");
    expect(detectValueKind("s3://bucket/a", "audio")).toBe("audio");
    expect(detectValueKind("s3://bucket/a.pdf", "document")).toBe("media");
    expect(detectValueKind(3)).toBe("number");
    expect(detectValueKind(true)).toBe("boolean");
  });

  it("parses JSON strings safely", () => {
    expect(parseJsonString("{bad")).toBeUndefined();
    expect(parseJsonString("[1,2]")).toEqual([1, 2]);
    expect(parseJsonString("42")).toBeUndefined();
    expect(isChatArray([{ role: "assistant", tool_calls: [] }])).toBe(true);
    expect(isChatArray([{ content: "x" }])).toBe(false);
  });

  it("clamps text and prints raw values", () => {
    expect(clampText("abc", 10)).toEqual({
      text: "abc",
      clamped: false,
      length: 3,
    });
    const c = clampText("x".repeat(20), 5);
    expect(c.clamped).toBe(true);
    expect(c.length).toBe(20);
    expect(rawText({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(rawText(null)).toBe("");
  });
});

describe("textDiff", () => {
  it("marks removed and added words", () => {
    const lines = diffLines(
      "refund in three business days",
      "refund in seven business days",
    );
    expect(lines).toHaveLength(1);
    const flat = lines[0];
    expect(flat.find((s) => s.status === "removed").text).toBe("three");
    expect(flat.find((s) => s.status === "added").text).toBe("seven");
    expect(flat.filter((s) => !s.status).map((s) => s.text)).toEqual([
      "refund",
      "in",
      "business",
      "days",
    ]);
    expect(diffStats(lines)).toEqual({ added: 1, removed: 1 });
  });

  it("replaces whole words, never fragments", () => {
    const [line] = diffLines("in 3–5 days", "in 5–7 days");
    expect(line).toEqual([
      { text: "in" },
      { text: "3–5", status: "removed" },
      { text: "5–7", status: "added" },
      { text: "days" },
    ]);
  });

  it("keeps line breaks as separate lines", () => {
    const lines = diffLines("a\nb", "a\nc");
    expect(lines.length).toBe(2);
  });

  it("diffs JSON by line", () => {
    const lines = diffLines(
      { name: "lookup", id: 1 },
      { name: "refund", id: 1 },
      "json",
    );
    const statuses = lines
      .flat()
      .map((s) => s.status)
      .filter(Boolean);
    expect(statuses).toContain("added");
    expect(statuses).toContain("removed");
  });

  it("handles empty sides", () => {
    expect(diffStats(diffLines("", "new text"))).toEqual({
      added: 2,
      removed: 0,
    });
    expect(diffLines(null, null)).toEqual([[]]);
  });
});

describe("useValueViewMode", () => {
  it("remembers the mode and syncs every viewer", () => {
    const mem = new Map();
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: (k) => (mem.has(k) ? mem.get(k) : null),
        setItem: (k, v) => mem.set(k, String(v)),
        removeItem: (k) => mem.delete(k),
      },
    });
    const a = renderHook(() => useValueViewMode());
    const b = renderHook(() => useValueViewMode());
    expect(a.result.current[0]).toBe("formatted");
    act(() => a.result.current[1]("raw"));
    expect(b.result.current[0]).toBe("raw");
    expect(window.localStorage.getItem(VALUE_VIEW_MODE_KEY)).toBe("raw");
    act(() => b.result.current[1]((m) => (m === "raw" ? "formatted" : "raw")));
    expect(a.result.current[0]).toBe("formatted");
    act(() => a.result.current[1]("bogus"));
    expect(a.result.current[0]).toBe("formatted");
  });
});

describe("ValueViewer / TextDiff / FieldCard render", () => {
  it("clamps long text with Show all", () => {
    render(<ValueViewer value={"word ".repeat(400)} clampChars={100} />);
    fireEvent.click(
      screen.getByRole("button", { name: /Show all \(2,000 chars\)/ }),
    );
    expect(
      screen.getByRole("button", { name: "Show less" }),
    ).toBeInTheDocument();
  });

  it("shows raw values in a pre and empty values in italics", () => {
    const { container } = render(
      <>
        <ValueViewer value={{ a: 1 }} mode="raw" />
        <ValueViewer value="" />
      </>,
    );
    expect(container.querySelector("pre").textContent).toContain('"a": 1');
    expect(screen.getByText("empty")).toBeInTheDocument();
  });

  it("renders chat messages with roles", () => {
    render(
      <ValueViewer
        value={[
          { role: "user", content: "Order A-7781 arrived cracked" },
          { role: "assistant", content: "Sorry! Refunded $64.50." },
        ]}
      />,
    );
    // Rendered by the trace chat view (ChatMessageView), with its role labels.
    expect(screen.getByText(/^User$/i)).toBeInTheDocument();
    expect(screen.getByText("Sorry! Refunded $64.50.")).toBeInTheDocument();
  });

  it("renders a word diff through GenerateDiffText", () => {
    render(<TextDiff from="takes three days" to="takes seven days" />);
    expect(screen.getByText("three")).toBeInTheDocument();
    expect(screen.getByText("seven")).toBeInTheDocument();
  });

  it("FieldCard shows edited state and fires edit and revert", () => {
    let edits = 0;
    let reverts = 0;
    render(
      <FieldCard
        name="expected_reply"
        role="expected"
        value="Sorry nobody called"
        editable
        edited
        onEdit={() => (edits += 1)}
        onRevert={() => (reverts += 1)}
      />,
    );
    expect(screen.getByText("Edited")).toBeInTheDocument();
    expect(screen.getByText("Expected")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Edit expected_reply" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Revert" }));
    expect(edits).toBe(1);
    expect(reverts).toBe(1);
  });
});
