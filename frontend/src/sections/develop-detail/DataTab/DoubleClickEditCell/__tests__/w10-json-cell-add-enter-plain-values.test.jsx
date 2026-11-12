import React, { createRef } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import JsonCellEditor from "../JsonCellEditor";

// Keep the editor, the Add form, state and both commit paths real. Only the
// viewer callbacks and the grid boundary (stopEditing/getValue and the popup
// editor's own keydown listener) are simulated.
const viewer = vi.hoisted(() => ({ nextValue: undefined }));

/* eslint-disable react/prop-types */
vi.mock("@textea/json-viewer", () => ({
  defineDataType: (config) => config,
  JsonViewer: ({ value, enableAdd, onAdd, onChange }) => (
    <div>
      <pre data-testid="viewer-value">{JSON.stringify(value)}</pre>
      <button
        type="button"
        disabled={!enableAdd([], value)}
        onClick={() => onAdd([])}
      >
        Add top-level field
      </button>
      {typeof value !== "object" && (
        <button
          type="button"
          onClick={() => onChange([], value, viewer.nextValue)}
        >
          Edit value
        </button>
      )}
      {Array.isArray(value) && value.length > 0 && (
        <button
          type="button"
          onClick={() => onChange([0], value[0], viewer.nextValue)}
        >
          Edit first item
        </button>
      )}
    </div>
  ),
}));
/* eslint-enable react/prop-types */

beforeEach(() => {
  viewer.nextValue = undefined;
});

afterEach(cleanup);

const mountEditor = (value) => {
  const ref = createRef();
  const onCellValueChanged = vi.fn();
  const normalCommit = vi.fn();
  const api = {
    stopEditing: vi.fn((cancel) => {
      if (!cancel) normalCommit(ref.current.getValue());
    }),
  };
  // AG Grid renders a popup editor into its own wrapper element. The wrapper's
  // keydown listener is attached before React's and ends editing on Enter.
  const gridKeyDown = vi.fn();
  const popup = document.createElement("div");
  popup.addEventListener("keydown", (event) => gridKeyDown(event.key));
  document.body.appendChild(popup);
  const node = { data: { row_id: "row-1" } };
  const column = { getColId: () => "json-column" };
  const colDef = {
    field: "json-column",
    cellEditorParams: { onCellValueChanged },
  };
  const view = render(
    <JsonCellEditor
      ref={ref}
      value={value}
      api={api}
      node={node}
      column={column}
      colDef={colDef}
    />,
    { container: popup },
  );
  return { ...view, ref, api, gridKeyDown, onCellValueChanged, normalCommit };
};

const viewerValue = () =>
  JSON.parse(screen.getByTestId("viewer-value").textContent);

const openAddForm = () => {
  fireEvent.click(screen.getByRole("button", { name: "Add top-level field" }));
};

const fillField = (key, value) => {
  fireEvent.change(screen.getByLabelText("Key"), { target: { value: key } });
  fireEvent.change(screen.getByLabelText("Value"), { target: { value } });
};

const apply = () => {
  fireEvent.click(screen.getByRole("button", { name: "Apply Changes" }));
};

const pressEnter = (target = window) => {
  fireEvent.keyDown(target, { key: "Enter" });
};

const PLAIN_VALUES = [
  ["an empty array", "[]", []],
  ["a number", "5", 5],
  ["plain text", "hello", "hello"],
  ["a JSON string", '"hi"', "hi"],
  ["null", "null", null],
];

describe("W10-EXPERIMENTS-DATASETS-U01 JSON cell editor", () => {
  describe("Enter in the Add form", () => {
    it.each(["Key", "Value"])(
      "Enter in the %s field adds the field and keeps the cell open",
      (label) => {
        const editor = mountEditor('{"a":1}');
        openAddForm();
        fillField("b", "2");
        pressEnter(screen.getByLabelText(label));

        expect(editor.gridKeyDown).not.toHaveBeenCalled();
        expect(editor.api.stopEditing).not.toHaveBeenCalled();
        expect(screen.queryByLabelText("Key")).not.toBeInTheDocument();
        expect(viewerValue()).toEqual({ a: 1, b: 2 });

        apply();
        expect(editor.onCellValueChanged).toHaveBeenCalledExactlyOnceWith(
          expect.objectContaining({
            newValue: '{"a":1,"b":2}',
            oldValue: '{"a":1}',
          }),
        );
      },
    );

    it("Enter outside the fields keeps the open Add form and its values", () => {
      const editor = mountEditor('{"a":1}');
      openAddForm();
      fillField("b", "2");
      pressEnter();

      expect(editor.api.stopEditing).not.toHaveBeenCalled();
      expect(screen.getByLabelText("Key")).toHaveValue("b");
      expect(screen.getByLabelText("Value")).toHaveValue("2");
    });

    it("Enter after the field is added saves the cell with the new field", () => {
      const editor = mountEditor('{"a":1}');
      openAddForm();
      fillField("b", "2");
      pressEnter(screen.getByLabelText("Value"));
      pressEnter();

      expect(editor.normalCommit).toHaveBeenCalledExactlyOnceWith(
        '{"a":1,"b":2}',
      );
      expect(editor.onCellValueChanged).not.toHaveBeenCalled();
    });
  });

  describe("cells that hold a plain value, text or an empty array", () => {
    it.each(PLAIN_VALUES)(
      "shows %s without a root key and Enter saves it unchanged",
      (_label, cell, plain) => {
        const editor = mountEditor(cell);
        expect(viewerValue()).toEqual(plain);

        pressEnter();
        expect(editor.normalCommit).toHaveBeenCalledTimes(1);
        expect(JSON.parse(editor.normalCommit.mock.calls[0][0])).toEqual(plain);
      },
    );

    it.each(PLAIN_VALUES)(
      "Apply Changes on untouched %s writes nothing",
      (_label, cell) => {
        const editor = mountEditor(cell);
        apply();
        expect(editor.api.stopEditing).toHaveBeenCalledExactlyOnceWith(true);
        expect(editor.onCellValueChanged).not.toHaveBeenCalled();
      },
    );

    it("adding an item to an empty array saves an array", () => {
      const editor = mountEditor("[]");
      openAddForm();
      fillField("item", "x");
      fireEvent.click(screen.getByRole("button", { name: "Add", exact: true }));
      expect(viewerValue()).toEqual(["x"]);

      apply();
      expect(editor.onCellValueChanged).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ newValue: '["x"]', oldValue: "[]" }),
      );
    });

    it.each([
      ["a number", "5", 6, "6"],
      ["plain text", "hello", "hello world", '"hello world"'],
    ])(
      "editing %s saves the edited value itself",
      (_label, cell, nextValue, saved) => {
        viewer.nextValue = nextValue;
        const editor = mountEditor(cell);
        fireEvent.click(screen.getByRole("button", { name: "Edit value" }));
        expect(viewerValue()).toEqual(nextValue);

        apply();
        expect(editor.onCellValueChanged).toHaveBeenCalledExactlyOnceWith(
          expect.objectContaining({ newValue: saved }),
        );
      },
    );

    it("editing an item in an array keeps the cell an array", () => {
      viewer.nextValue = "y";
      const editor = mountEditor('["x"]');
      fireEvent.click(screen.getByRole("button", { name: "Edit first item" }));
      expect(viewerValue()).toEqual(["y"]);

      apply();
      expect(editor.onCellValueChanged).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ newValue: '["y"]' }),
      );
    });
  });
});
