/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import axios, { endpoints } from "src/utils/axios";
import FieldEditor from "../FieldEditor";
import { validateEdit } from "../editValue";
import { writeCellClassic } from "../../Versions/classicWrites";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("@monaco-editor/react", () => ({ Editor: () => null }));
vi.mock("src/sections/common/RowPanel/ShortcutSheet", () => ({
  KeyCap: ({ children }) => <kbd>{children}</kbd>,
}));
vi.mock("src/sections/develop/realBackendCaps", () => ({
  gridWritesClassic: () => true,
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: {
    develop: {
      updateCellValue: (id) => `/develops/${id}/update_cell_value/`,
    },
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({ data: { result: "Saved" } });
});
afterEach(cleanup);

describe("J09-W2-003 datetime row edits", () => {
  // The cell writer parses DateTimeFormatChoices and stores YYYY-MM-DD HH:mm:ss.
  // These are naive local wall times, not UTC instants: never apply an offset.
  it.each([
    ["D01 browser minutes", "2026-11-09T12:30", "2026-11-09 12:30:00"],
    ["D02 browser seconds", "2026-11-09T12:30:45", "2026-11-09 12:30:45"],
    ["D03 midnight", "2027-01-01T00:00", "2027-01-01 00:00:00"],
    ["D04 DST wall time", "2026-03-08T02:30", "2026-03-08 02:30:00"],
    ["D05 stored canonical", "2026-11-09 12:30:45", "2026-11-09 12:30:45"],
    ["D06 stored date only", "2026-11-09", "2026-11-09"],
    ["D07 empty stays empty", "", ""],
    ["D08 null stays empty", null, ""],
  ])("%s", (_id, draft, expected) => {
    expect(validateEdit(draft, "datetime")).toEqual({ ok: true, value: expected });
  });

  it("D09 leaves datetime-shaped text untouched", () => {
    expect(validateEdit("2026-11-09T12:30", "text")).toEqual({
      ok: true,
      value: "2026-11-09T12:30",
    });
  });

  it("D10 preserves other typed editor conversions", () => {
    expect(validateEdit("42", "integer")).toEqual({ ok: true, value: 42 });
    expect(validateEdit("true", "boolean")).toEqual({ ok: true, value: "true" });
    expect(validateEdit('{ "time": "2026-11-09T12:30" }', "json")).toEqual({
      ok: true,
      value: '{"time":"2026-11-09T12:30"}',
    });
  });

  it.each([
    ["D11 Apply", "button"],
    ["D12 keyboard Apply", "keyboard"],
  ])("%s sends the API's accepted wall time", (_id, trigger) => {
    const onApply = vi.fn((value) =>
      writeCellClassic({ datasetId: "dataset-1", rowId: "row-1", columnId: "time-1", value }),
    );
    const { container } = render(
      <FieldEditor dataType="datetime" value="" onApply={onApply} onCancel={vi.fn()} />,
    );
    const picker = container.querySelector('input[type="datetime-local"]');
    expect(picker).not.toBeNull();
    fireEvent.change(picker, { target: { value: "2026-11-09T12:30" } });
    expect(picker.value).toBe("2026-11-09T12:30");
    if (trigger === "button") fireEvent.click(screen.getByRole("button", { name: /Apply/ }));
    else fireEvent.keyDown(picker, { key: "Enter", ctrlKey: true });
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledWith(endpoints.develop.updateCellValue("dataset-1"), {
      row_id: "row-1",
      column_id: "time-1",
      new_value: "2026-11-09 12:30:00",
    });
  });

  it("D13 reopens and reapplies the stored value without shifting time", () => {
    const onApply = vi.fn();
    const { container } = render(
      <FieldEditor
        dataType="datetime"
        value="2026-11-09 12:30:45"
        onApply={onApply}
        onCancel={vi.fn()}
      />,
    );
    expect(container.querySelector('input[type="datetime-local"]').value).toContain("2026-11-09T12:30:45");
    fireEvent.click(screen.getByRole("button", { name: /Apply/ }));
    expect(onApply).toHaveBeenCalledWith("2026-11-09 12:30:45");
  });
});
