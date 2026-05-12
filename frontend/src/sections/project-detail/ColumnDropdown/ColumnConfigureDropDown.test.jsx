import React from "react";
import { describe, it, expect, vi } from "vitest";
import { fireEvent, waitFor } from "@testing-library/react";
import { render, screen } from "src/utils/test-utils";
import ColumnConfigureDropDown from "./ColumnConfigureDropDown";

const COLUMNS = [
  { id: "name", name: "Name", isVisible: true },
  { id: "status", name: "Status", isVisible: true },
  { id: "span.duration", name: "span.duration", isVisible: true },
  { id: "span.model", name: "span.model", isVisible: true },
];

describe("ColumnConfigureDropDown", () => {
  const renderPicker = (props = {}) =>
    render(
      <ColumnConfigureDropDown
        open
        onClose={vi.fn()}
        anchorEl={document.body}
        setColumns={vi.fn()}
        onColumnVisibilityChange={vi.fn()}
        {...props}
      />,
    );

  it("renders each column name in the list", () => {
    renderPicker({ columns: COLUMNS });
    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("span.duration")).toBeInTheDocument();
  });

  describe("select-all", () => {
    const allHidden = COLUMNS.map((c) => ({ ...c, isVisible: false }));
    const allVisible = COLUMNS.map((c) => ({ ...c, isVisible: true }));
    const mixed = COLUMNS.map((c, i) => ({ ...c, isVisible: i % 2 === 0 }));

    it("is unchecked when no columns are visible", () => {
      renderPicker({ columns: allHidden });
      const cb = screen.getByLabelText("Toggle Select all");
      expect(cb).not.toBeChecked();
      expect(cb.getAttribute("data-indeterminate")).not.toBe("true");
    });

    it("is checked when every column is visible", () => {
      renderPicker({ columns: allVisible });
      const cb = screen.getByLabelText("Toggle Select all");
      expect(cb).toBeChecked();
      expect(cb.getAttribute("data-indeterminate")).not.toBe("true");
    });

    it("is unchecked when selection is partial (binary, not indeterminate)", () => {
      renderPicker({ columns: mixed });
      const cb = screen.getByLabelText("Toggle Select all");
      expect(cb).not.toBeChecked();
      expect(cb.getAttribute("data-indeterminate")).not.toBe("true");
    });

    it("selects every column when clicked while partial", () => {
      const onColumnVisibilityChange = vi.fn();
      renderPicker({ columns: mixed, onColumnVisibilityChange });

      fireEvent.click(screen.getByLabelText("Toggle Select all"));

      expect(onColumnVisibilityChange).toHaveBeenCalledTimes(1);
      const updated = onColumnVisibilityChange.mock.calls[0][0];
      COLUMNS.forEach((c) => expect(updated[c.id]).toBe(true));
    });

    it("deselects every column when clicked while all-selected", () => {
      const onColumnVisibilityChange = vi.fn();
      renderPicker({ columns: allVisible, onColumnVisibilityChange });

      fireEvent.click(screen.getByLabelText("Toggle Select all"));

      expect(onColumnVisibilityChange).toHaveBeenCalledTimes(1);
      const updated = onColumnVisibilityChange.mock.calls[0][0];
      COLUMNS.forEach((c) => expect(updated[c.id]).toBe(false));
    });

    it("only toggles columns matching the current search", async () => {
      const onColumnVisibilityChange = vi.fn();
      renderPicker({ columns: allHidden, onColumnVisibilityChange });

      fireEvent.change(screen.getByPlaceholderText("Search"), {
        target: { value: "span" },
      });

      await waitFor(() =>
        expect(screen.queryByText("Name")).not.toBeInTheDocument(),
      );

      fireEvent.click(screen.getByLabelText("Toggle Select all"));

      const updated = onColumnVisibilityChange.mock.calls[0][0];
      expect(updated["span.duration"]).toBe(true);
      expect(updated["span.model"]).toBe(true);
      expect(updated.name).toBe(false);
      expect(updated.status).toBe(false);
    });

    it("is hidden when search filters to zero results", async () => {
      renderPicker({ columns: COLUMNS });

      fireEvent.change(screen.getByPlaceholderText("Search"), {
        target: { value: "nothing-matches" },
      });

      await waitFor(() =>
        expect(screen.getByText("No columns found")).toBeInTheDocument(),
      );
      expect(screen.queryByLabelText("Toggle Select all")).not.toBeInTheDocument();
    });
  });
});
