/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import useShortcuts, {
  unavailableFor,
  useShortcutStore,
} from "../useShortcuts";
import ShortcutSheet from "src/sections/common/RowPanel/ShortcutSheet";
import useRowKeyboardNav from "src/sections/common/RowPanel/useRowKeyboardNav";

function Grid({ handlers }) {
  useShortcuts("grid", handlers);
  return (
    <div>
      <button type="button" role="tab">
        Runs across experiments
      </button>
      <input aria-label="Search rows" />
    </div>
  );
}

function RowPanel({ onNext }) {
  useRowKeyboardNav({ onNext, surface: "row" });
  return (
    <button type="button" role="tab">
      Runs across experiments
    </button>
  );
}

afterEach(() => useShortcutStore.setState({ stack: [], paletteOpen: false }));

describe("useShortcuts", () => {
  it("RV#1: after clicking a tab, J still moves the row", () => {
    const onNext = vi.fn();
    render(<RowPanel onNext={onNext} />);
    const tab = screen.getByRole("tab");
    fireEvent.click(tab);
    tab.focus();
    fireEvent.keyDown(document.activeElement, { key: "j" });
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  it("binds by action, registers the surface, and never fires while typing", () => {
    const next = vi.fn();
    const density = vi.fn();
    const { unmount } = render(<Grid handlers={{ next, density }} />);
    expect(useShortcutStore.getState().current()).toBe("grid");
    screen.getByRole("tab").focus();
    fireEvent.keyDown(document.activeElement, { key: "d" });
    expect(density).toHaveBeenCalledTimes(1);
    screen.getByLabelText("Search rows").focus();
    fireEvent.keyDown(document.activeElement, { key: "j" });
    expect(next).not.toHaveBeenCalled();
    unmount();
    expect(useShortcutStore.getState().current()).toBeNull();
  });

  it("Enter on a focused button activates the button, not the shortcut", () => {
    const open = vi.fn();
    render(<Grid handlers={{ open }} />);
    screen.getByRole("tab").focus();
    fireEvent.keyDown(document.activeElement, { key: "Enter" });
    expect(open).not.toHaveBeenCalled();
    document.activeElement.blur();
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("goes quiet while the palette is open", () => {
    const next = vi.fn();
    render(<Grid handlers={{ next }} />);
    useShortcutStore.getState().setPaletteOpen(true);
    fireEvent.keyDown(document.body, { key: "j" });
    expect(next).not.toHaveBeenCalled();
  });
});

describe("routeSurface", async () => {
  const { surfaceForRoute } = await import("../routeSurface");
  const TS = "d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06";
  it("maps routes to keymap surfaces", () => {
    expect(surfaceForRoute(`/dashboard/develop/${TS}`, "?tab=data")).toBe(
      "grid",
    );
    expect(
      surfaceForRoute(`/dashboard/develop/${TS}`, "?tab=experiments"),
    ).toBe("leaderboard");
    expect(surfaceForRoute(`/dashboard/develop/${TS}`, "?tab=data&row=x")).toBe(
      "row",
    );
    expect(surfaceForRoute(`/dashboard/develop/${TS}/compare/matrix`)).toBe(
      "matrix",
    );
    expect(surfaceForRoute(`/dashboard/develop/${TS}/compare/tasks`)).toBe(
      "taskCompare",
    );
    expect(surfaceForRoute(`/dashboard/develop/${TS}/compare/data`)).toBe(
      "results",
    );
    expect(surfaceForRoute("/dashboard/develop")).toBe("list");
    expect(surfaceForRoute("/dashboard/observe")).toBeNull();
  });

  it("unavailable keys are named per surface and greyed in the sheet", () => {
    function TC({ off }) {
      useShortcuts("taskCompare", { diffOnly: () => {} }, { unavailable: off });
      return null;
    }
    const { rerender } = render(<TC off={{ diffOnly: "Needs two lanes" }} />);
    expect(unavailableFor("taskCompare")).toEqual({
      diffOnly: "Needs two lanes",
    });
    expect(unavailableFor("grid")).toEqual({});
    render(<ShortcutSheet open onClose={() => {}} surface="taskCompare" />);
    const row = document.querySelector('[data-shortcut="diffOnly"]');
    expect(row?.getAttribute("data-unavailable")).toBe("true");
    expect(row?.textContent).toContain("Needs two lanes");
    // R4 (D25): an action this screen has no handler for is hidden.
    expect(document.querySelector('[data-shortcut="nextDiff"]')).toBeNull();
    // Two lanes again → {} → nothing greyed (read when the sheet renders).
    rerender(<TC off={{}} />);
    expect(unavailableFor("taskCompare")).toEqual({});
  });
});
