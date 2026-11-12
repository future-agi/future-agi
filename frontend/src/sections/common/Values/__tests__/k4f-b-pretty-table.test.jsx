// integration review fix (RV-02): Pretty on a wrapped conversation is never a blank
// view. PrettyTable reads an object by key and a list by index, so the
// ValueViewer's Pretty mode shows the value whether it is handed the whole
// session object or only its messages.
import { describe, expect, it } from "vitest";
import React from "react";
import { render } from "@testing-library/react";
import PrettyTable from "../PrettyTable";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";

const messages = [
  { role: "user", content: "Where is my refund?" },
  { role: "assistant", content: "It was issued on Monday." },
];
const session = { channel: "chat", messages, resolution: "Pass" };

describe("RV-02 · Pretty is never blank", () => {
  it("an object: one row per key", () => {
    const { container } = render(<PrettyTable value={session} />);
    const t = container.textContent;
    ["channel", "messages", "resolution", "Pass"].forEach((x) =>
      expect(t).toContain(x),
    );
  });

  it("a list: one row per index", () => {
    const { container } = render(<PrettyTable value={messages} />);
    const rows = container.querySelectorAll("tbody > tr");
    expect(rows.length).toBe(2);
    expect(container.textContent).toContain("[0]");
    expect(container.textContent).toContain("[1]");
  });

  it("the viewer's Pretty mode on a wrapped conversation renders rows", () => {
    const { container } = render(
      <ValueViewer value={session} view="pretty" toolbar={false} />,
    );
    expect(container.querySelectorAll("tbody > tr").length).toBeGreaterThan(0);
  });
});
