// integration review 2 (nit): Pretty cuts a long list at the shared BIG_LIST (the
// same limit as ValueViewer's Auto tree) and says so: `first 200 of 350
// items`, at the top level and inside an opened nested list; a list at or
// under the limit shows every item and no cut line.
import { describe, expect, it } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import PrettyTable from "../PrettyTable";
import { BIG_LIST, cutListText } from "../viewModes";

const list = (n) => Array.from({ length: n }, (_, i) => `item ${i + 1}`);

describe("Pretty · a cut list says so", () => {
  it("top level: BIG_LIST rows and the cut line", () => {
    const n = BIG_LIST + 150;
    const { container } = render(<PrettyTable value={list(n)} />);
    const rows = container.querySelectorAll("tbody > tr");
    expect(rows.length).toBe(BIG_LIST + 1);
    expect(screen.getByTestId("pretty-list-cut").textContent).toBe(
      cutListText(n),
    );
    expect(cutListText(n)).toBe(`first ${BIG_LIST} of ${n} items`);
  });

  it("a nested list, opened: its cut line under its items", () => {
    const n = BIG_LIST + 1;
    render(<PrettyTable value={{ title: "t", items: list(n) }} />);
    expect(screen.queryByTestId("pretty-list-cut")).toBeNull();
    fireEvent.click(screen.getByText("items"));
    expect(screen.getByTestId("pretty-list-cut").textContent).toBe(
      cutListText(n),
    );
  });

  it("a list at the limit: every item, no cut line", () => {
    const { container } = render(<PrettyTable value={list(BIG_LIST)} />);
    expect(container.querySelectorAll("tbody > tr").length).toBe(BIG_LIST);
    expect(screen.queryByTestId("pretty-list-cut")).toBeNull();
  });
});
