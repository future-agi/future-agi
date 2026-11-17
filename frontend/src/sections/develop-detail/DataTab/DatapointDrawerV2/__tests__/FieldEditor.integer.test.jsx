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

function editor(value = "") {
  const onApply = vi.fn((next) =>
    writeCellClassic({
      datasetId: "dataset-1",
      rowId: "row-1",
      columnId: "integer-1",
      value: next,
    }),
  );
  render(
    <FieldEditor
      dataType="integer"
      value={value}
      onApply={onApply}
      onCancel={vi.fn()}
    />,
  );
  return { onApply, input: screen.getByRole("spinbutton") };
}

describe("exact integer edits", () => {
  it.each([
    ["9007199254740991", "9007199254740991"],
    ["9007199254740992", "9007199254740992"],
    ["9007199254740993", "9007199254740993"],
    ["-9007199254740993", "-9007199254740993"],
    ["9007199254740993.0", "9007199254740993"],
    ["9.007199254740993e15", "9007199254740993"],
    ["3.0", "3"],
    ["1e3", "1000"],
    ["-0.0", "0"],
    ["", ""],
  ])("sends %s as exact integer text", (draft, expected) => {
    const { onApply, input } = editor();
    fireEvent.change(input, { target: { value: draft } });
    expect(input.value).toBe(draft);
    fireEvent.click(screen.getByRole("button", { name: /Apply/ }));
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledWith(
      endpoints.develop.updateCellValue("dataset-1"),
      {
        row_id: "row-1",
        column_id: "integer-1",
        new_value: expected,
      },
    );
  });

  it("reapplies a stored large integer exactly with the keyboard", () => {
    const { input } = editor("9007199254740993");
    expect(input.value).toBe("9007199254740993");
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledWith(
      endpoints.develop.updateCellValue("dataset-1"),
      {
        row_id: "row-1",
        column_id: "integer-1",
        new_value: "9007199254740993",
      },
    );
  });

  it.each(["3.5", "9007199254740993.1", "1.0000000000000001", "1e-400"])(
    "refuses fractional %s without sending a request",
    (draft) => {
      const { onApply, input } = editor();
      fireEvent.change(input, { target: { value: draft } });
      expect(input.value).toBe(draft);
      expect(screen.getByText("Enter a whole number")).toBeTruthy();
      const apply = screen.getByRole("button", { name: /Apply/ });
      expect(apply.disabled).toBe(true);
      fireEvent.click(apply);
      fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
      expect(onApply).not.toHaveBeenCalled();
      expect(axios.post).not.toHaveBeenCalled();
    },
  );

  it.each(["NaN", "Infinity", "0x10", "1_000", "1e100000"])(
    "rejects unsupported integer text %s clearly",
    (draft) => {
      expect(validateEdit(draft, "integer").ok).toBe(false);
      expect(validateEdit(draft, "integer").error).toMatch(/whole number/);
    },
  );

  it("retains safe numbers, decimal normalization and empty values", () => {
    expect(validateEdit("42", "integer")).toEqual({ ok: true, value: 42 });
    expect(validateEdit("  +00042  ", "integer")).toEqual({
      ok: true,
      value: 42,
    });
    expect(validateEdit("0.01e2", "integer")).toEqual({ ok: true, value: 1 });
    expect(validateEdit("1200e-2", "integer")).toEqual({ ok: true, value: 12 });
    expect(validateEdit(" \t\n", "integer")).toEqual({ ok: true, value: "" });
    expect(validateEdit("3.5", "float")).toEqual({ ok: true, value: 3.5 });
    expect(validateEdit("3.5", "number")).toEqual({ ok: true, value: 3.5 });
  });
});
