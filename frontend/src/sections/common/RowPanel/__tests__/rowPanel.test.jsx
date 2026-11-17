import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderHook, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import useRowKeyboardNav, { resolveRowKey } from "../useRowKeyboardNav";
import RowPanelShell from "../RowPanelShell";
import ShortcutSheet from "../ShortcutSheet";
import ActivityList from "../ActivityList";

const press = (key, opts = {}, target = document.body) =>
  fireEvent.keyDown(target, { key, bubbles: true, ...opts });

describe("useRowKeyboardNav", () => {
  it("maps J/K, arrows, Esc, F and extra keys", () => {
    const h = {
      onNext: vi.fn(),
      onPrev: vi.fn(),
      onClose: vi.fn(),
      onFull: vi.fn(),
      keys: { diff: vi.fn(), r: vi.fn(), help: vi.fn() },
    };
    renderHook(() => useRowKeyboardNav({ enabled: true, ...h }));
    press("j");
    press("ArrowDown");
    press("k");
    press("ArrowUp");
    // iterate r3 G3: ← / → no longer mean next / previous.
    press("ArrowRight");
    press("ArrowLeft");
    press("Escape");
    press("f");
    press("d");
    press("r");
    press("?", { shiftKey: true });
    expect(h.onNext).toHaveBeenCalledTimes(2);
    expect(h.onPrev).toHaveBeenCalledTimes(2);
    expect(h.onClose).toHaveBeenCalledTimes(1);
    expect(h.onFull).toHaveBeenCalledTimes(1);
    expect(h.keys.diff).toHaveBeenCalledTimes(1);
    expect(h.keys.r).toHaveBeenCalledTimes(1);
    expect(h.keys.help).toHaveBeenCalledTimes(1);
  });

  it("ignores text fields, keeps arrows off the grid, allows J from a cell", () => {
    const onNext = vi.fn();
    renderHook(() => useRowKeyboardNav({ enabled: true, onNext }));
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();
    press("j", {}, input);
    expect(onNext).not.toHaveBeenCalled();
    input.remove();

    const grid = document.createElement("div");
    grid.className = "ag-root-wrapper";
    const cell = document.createElement("div");
    cell.setAttribute("role", "gridcell");
    cell.tabIndex = -1;
    grid.append(cell);
    document.body.append(grid);
    cell.focus();
    press("ArrowDown", {}, cell);
    expect(onNext).not.toHaveBeenCalled();
    press("j", {}, cell);
    expect(onNext).toHaveBeenCalledTimes(1);
    grid.remove();

    // ↓ stays with elements that use vertical arrows themselves.
    const radio = document.createElement("input");
    radio.type = "radio";
    document.body.append(radio);
    radio.focus();
    press("ArrowDown", {}, radio);
    expect(onNext).toHaveBeenCalledTimes(1);
    radio.remove();
    const listbox = document.createElement("ul");
    listbox.setAttribute("role", "listbox");
    const option = document.createElement("li");
    option.setAttribute("role", "option");
    option.tabIndex = -1;
    listbox.append(option);
    document.body.append(listbox);
    option.focus();
    press("ArrowDown", {}, option);
    expect(onNext).toHaveBeenCalledTimes(1);
    listbox.remove();
    // A button (a clicked chip or tab) keeps ↓ for the rows.
    const button = document.createElement("button");
    document.body.append(button);
    button.focus();
    press("ArrowDown", {}, button);
    expect(onNext).toHaveBeenCalledTimes(2);
    button.remove();
  });

  it("fires ⌘↵ submit even from a text field, and nothing when disabled", () => {
    const submit = vi.fn();
    const onNext = vi.fn();
    const { rerender } = renderHook((p) => useRowKeyboardNav(p), {
      initialProps: { enabled: true, keys: { submit }, onNext },
    });
    const ta = document.createElement("textarea");
    document.body.append(ta);
    ta.focus();
    press("Enter", { metaKey: true }, ta);
    expect(submit).toHaveBeenCalledTimes(1);
    ta.remove();
    rerender({ enabled: false, keys: { submit }, onNext });
    press("j");
    expect(onNext).not.toHaveBeenCalled();
  });

  it("resolves keys by action name before literal keys", () => {
    const diff = vi.fn();
    expect(resolveRowKey({ key: "d" }, { keys: { diff } })).toBe(diff);
    expect(resolveRowKey({ key: "x" }, { keys: {} })).toBeNull();
  });
});

describe("RowPanelShell", () => {
  it("renders only the grid when closed", () => {
    render(
      <RowPanelShell open={false} onClose={() => {}} grid={<div>GRID</div>}>
        panel
      </RowPanelShell>,
    );
    expect(screen.getByText("GRID")).toBeInTheDocument();
    expect(screen.queryByText("panel")).toBeNull();
  });

  it("renders the header, position, tabs and wires prev/next/close", () => {
    const onPrev = vi.fn();
    const onNext = vi.fn();
    const onClose = vi.fn();
    const onTab = vi.fn();
    render(
      <RowPanelShell
        open
        onClose={onClose}
        onToggleFull={() => {}}
        header={{
          title: "Row 12",
          position: { index: 12, total: 71, filtered: true },
          onPrev,
          onNext,
          nextDisabled: false,
          line2: <span>smoke</span>,
        }}
        tabs={[
          { value: "row", label: "Row" },
          { value: "runs", label: "Runs across experiments", count: 9 },
        ]}
        tab="row"
        onTab={onTab}
        grid={<div>GRID</div>}
      >
        <div>Row body</div>
      </RowPanelShell>,
    );
    expect(screen.getByText("Row 12")).toBeInTheDocument();
    expect(screen.getByText("12 of 71 filtered")).toBeInTheDocument();
    expect(screen.getByText("Runs across experiments (9)")).toBeInTheDocument();
    expect(screen.getByText("GRID")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Previous row" }));
    fireEvent.click(screen.getByRole("button", { name: "Next row" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByText("Runs across experiments (9)"));
    expect(onPrev).toHaveBeenCalled();
    expect(onNext).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
    expect(onTab).toHaveBeenCalledWith("runs");
  });

  it("shows `– of 71` for a row outside the set", () => {
    render(
      <RowPanelShell
        open
        full
        onClose={() => {}}
        header={{ title: "Row 241", position: { index: null, total: 71 } }}
      >
        x
      </RowPanelShell>,
    );
    expect(screen.getByText("– of 71")).toBeInTheDocument();
  });
});

describe("ShortcutSheet and ActivityList", () => {
  it("lists the row keys", () => {
    render(<ShortcutSheet open onClose={() => {}} surface="row" />);
    // R3: one grouped sheet — the surface first, then Global
    expect(screen.getByText("Row view")).toBeInTheDocument();
    expect(screen.getByText("Global")).toBeInTheDocument();
    expect(screen.getByText("Next row")).toBeInTheDocument();
    expect(screen.getByText("Apply edit")).toBeInTheDocument();
    expect(screen.getByText("Copy row id")).toBeInTheDocument();
    expect(
      screen.getByText("Command palette: go to, search, run a command"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Skip this item")).toBeNull();
    expect(screen.getAllByText("Navigate").length).toBeGreaterThan(0);
  });

  it("describes N8 events and expands an edit diff", () => {
    render(
      <ActivityList
        events={[
          {
            type: "edit",
            version: 3,
            actor: "Asha Rao",
            at: "2026-10-10T10:00:00Z",
            column: "expected_reply",
            before: "three business days",
            after: "seven business days",
          },
          {
            type: "splits",
            version: 4,
            actor: "Meera Iyer",
            added: ["hard"],
            removed: [],
          },
          {
            type: "created",
            version: 1,
            source: { type: "upload", file_name: "support-golden-sep.csv" },
          },
          {
            type: "queue",
            queue: "Support QA review",
            actor: "Ravi Menon",
            at: "2026-10-11T10:00:00Z",
          },
          {
            type: "score",
            label: "Correct?",
            value: "down",
            actor: "Meera Iyer",
            at: "2026-10-11T11:00:00Z",
          },
          {
            type: "run",
            run_label: "#2A",
            result: "Failed Correctness",
            at: "2026-10-09T10:00:00Z",
          },
        ]}
      />,
    );
    expect(screen.getByText(/changed expected_reply/)).toBeInTheDocument();
    expect(screen.getByText(/added to hard/)).toBeInTheDocument();
    expect(
      screen.getByText("Created from upload support-golden-sep.csv"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/added to queue “Support QA review”/),
    ).toBeInTheDocument();
    expect(screen.getByText("#2A")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Show diff/ }));
    expect(screen.getByText("three")).toBeInTheDocument();
  });

  it("has an empty state", () => {
    render(<ActivityList events={[]} />);
    expect(
      screen.getByText("No activity on this row yet."),
    ).toBeInTheDocument();
  });
});
