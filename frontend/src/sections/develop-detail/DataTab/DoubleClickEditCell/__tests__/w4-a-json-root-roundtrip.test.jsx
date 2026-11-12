import React, { createRef } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import JsonCellEditor from "../JsonCellEditor";

// Keep the editor, form controls, state and serializers real. Only the viewer
// callbacks and the grid's stopEditing/getValue boundary are simulated.
/* eslint-disable react/prop-types */
vi.mock("@textea/json-viewer", () => ({
  defineDataType: (config) => config,
  JsonViewer: ({ value, enableAdd, onAdd, onChange, onDelete }) => (
    <div>
      <pre data-testid="viewer-value">{JSON.stringify(value)}</pre>
      <button
        type="button"
        disabled={!enableAdd([], value)}
        onClick={() => onAdd([])}
      >
        Add top-level field
      </button>
      <button
        type="button"
        disabled={!enableAdd(["root"], value.root)}
        onClick={() => onAdd(["root"])}
      >
        Add root field
      </button>
      <button
        type="button"
        onClick={() => onChange(["root"], value.root, { status: "ready" })}
      >
        Replace root object
      </button>
      <button type="button" onClick={() => onDelete(["root"])}>
        Delete root field
      </button>
    </div>
  ),
}));
/* eslint-enable react/prop-types */

afterEach(cleanup);

const mountEditor = (value = "{}") => {
  const ref = createRef();
  const onCellValueChanged = vi.fn();
  const normalCommit = vi.fn();
  const api = {
    stopEditing: vi.fn((cancel) => {
      if (!cancel) normalCommit(ref.current.getValue());
    }),
  };
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
  );
  return { ...view, ref, api, onCellValueChanged, normalCommit };
};

const openAddForm = (button = "Add top-level field") => {
  fireEvent.click(screen.getByRole("button", { name: button }));
};

const fillField = (key = "status", value = "ready") => {
  fireEvent.change(screen.getByLabelText("Key"), { target: { value: key } });
  fireEvent.change(screen.getByLabelText("Value"), { target: { value } });
};

const addField = (button = "Add top-level field", key = "status", value = "ready") => {
  openAddForm(button);
  fillField(key, value);
  fireEvent.click(screen.getByRole("button", { name: "Add", exact: true }));
};

const apply = () => {
  fireEvent.click(screen.getByRole("button", { name: "Apply Changes" }));
};

const finishNormalCommit = (editor, method) => {
  if (method === "Enter") {
    fireEvent.keyDown(window, { key: "Enter" });
  } else {
    // The grid reads getValue when focus loss ends editing.
    editor.api.stopEditing();
  }
};

describe("W4-A-001 JSON cell root round trip", () => {
  it.each([
    ["JSON text", "{}"],
    ["an object", {}],
  ])("getValue preserves an empty object supplied as %s", (_label, value) => {
    const editor = mountEditor(value);
    expect(JSON.parse(editor.ref.current.getValue())).toEqual({});
    expect(editor.onCellValueChanged).not.toHaveBeenCalled();
  });

  it.each(["Enter", "focus loss"])(
    "an untouched empty object stays empty on %s",
    (method) => {
      const editor = mountEditor();
      finishNormalCommit(editor, method);
      expect(editor.normalCommit).toHaveBeenCalledExactlyOnceWith("{}");
      expect(editor.onCellValueChanged).not.toHaveBeenCalled();
    },
  );

  it.each(["Apply", "Enter", "focus loss"])(
    "adding a top-level field commits only that field via %s",
    (method) => {
      const editor = mountEditor();
      addField();
      expect(JSON.parse(editor.ref.current.getValue())).toEqual({ status: "ready" });

      if (method === "Apply") {
        apply();
        expect(editor.api.stopEditing).toHaveBeenCalledExactlyOnceWith(true);
        expect(editor.normalCommit).not.toHaveBeenCalled();
        expect(editor.onCellValueChanged).toHaveBeenCalledTimes(1);
        expect(editor.onCellValueChanged).toHaveBeenCalledWith(
          expect.objectContaining({
            newValue: editor.ref.current.getValue(),
            oldValue: "{}",
          }),
        );
      } else {
        finishNormalCommit(editor, method);
        expect(editor.normalCommit).toHaveBeenCalledExactlyOnceWith('{"status":"ready"}');
        expect(editor.onCellValueChanged).not.toHaveBeenCalled();
      }
    },
  );

  it("adding through the empty-object placeholder edits the actual object", () => {
    const editor = mountEditor();
    addField("Add root field");
    expect(JSON.parse(editor.ref.current.getValue())).toEqual({ status: "ready" });
    apply();
    expect(editor.onCellValueChanged).toHaveBeenCalledWith(
      expect.objectContaining({ newValue: '{"status":"ready"}' }),
    );
  });

  it("replacing the empty-object placeholder edits the actual object", () => {
    const editor = mountEditor();
    fireEvent.click(screen.getByRole("button", { name: "Replace root object" }));
    expect(JSON.parse(editor.ref.current.getValue())).toEqual({ status: "ready" });
    apply();
    expect(editor.onCellValueChanged).toHaveBeenCalledWith(
      expect.objectContaining({ newValue: '{"status":"ready"}' }),
    );
  });

  it("canceling the add form leaves the empty object unchanged", () => {
    const editor = mountEditor();
    openAddForm();
    fillField();
    fireEvent.click(screen.getByRole("button", { name: "Cancel", exact: true }));
    expect(JSON.parse(editor.ref.current.getValue())).toEqual({});
    apply();
    expect(editor.api.stopEditing).toHaveBeenCalledExactlyOnceWith(true);
    expect(editor.normalCommit).not.toHaveBeenCalled();
    expect(editor.onCellValueChanged).not.toHaveBeenCalled();
  });

  it("applying an untouched empty object does not write a presentation-only change", () => {
    const editor = mountEditor();
    apply();
    expect(editor.api.stopEditing).toHaveBeenCalledExactlyOnceWith(true);
    expect(editor.normalCommit).not.toHaveBeenCalled();
    expect(editor.onCellValueChanged).not.toHaveBeenCalled();
    expect(JSON.parse(editor.ref.current.getValue())).toEqual({});
  });

  it("grid cancellation does not submit draft fields", () => {
    const editor = mountEditor();
    addField();
    editor.api.stopEditing(true);
    editor.unmount();
    expect(editor.normalCommit).not.toHaveBeenCalled();
    expect(editor.onCellValueChanged).not.toHaveBeenCalled();
  });

  it.each([
    ["populated", { root: { existing: 1 } }],
    ["empty", { root: {} }],
  ])("preserves a genuine %s root key in both commit values", (_label, original) => {
    const editor = mountEditor(JSON.stringify(original));
    expect(JSON.parse(editor.ref.current.getValue())).toEqual(original);
    addField();
    const expected = { ...original, status: "ready" };
    expect(JSON.parse(editor.ref.current.getValue())).toEqual(expected);
    apply();
    expect(editor.onCellValueChanged).toHaveBeenCalledWith(
      expect.objectContaining({ newValue: JSON.stringify(expected) }),
    );
  });

  it("allows a genuine root key to be added to an empty object", () => {
    const editor = mountEditor();
    addField("Add top-level field", "root", '{"existing":1}');
    expect(JSON.parse(editor.ref.current.getValue())).toEqual({ root: { existing: 1 } });
    apply();
    expect(editor.onCellValueChanged).toHaveBeenCalledWith(
      expect.objectContaining({ newValue: '{"root":{"existing":1}}' }),
    );
  });

  it("keeps edits inside a genuine root key nested", () => {
    const editor = mountEditor('{"root":{"existing":1}}');
    addField("Add root field");
    const expected = { root: { existing: 1, status: "ready" } };
    expect(JSON.parse(editor.ref.current.getValue())).toEqual(expected);
    apply();
    expect(editor.onCellValueChanged).toHaveBeenCalledWith(
      expect.objectContaining({ newValue: JSON.stringify(expected) }),
    );
  });

  it("deleting a genuine root key returns to an empty object without inventing another", () => {
    const editor = mountEditor('{"root":{"existing":1}}');
    fireEvent.click(screen.getByRole("button", { name: "Delete root field" }));
    expect(JSON.parse(editor.ref.current.getValue())).toEqual({});
    apply();
    expect(editor.onCellValueChanged).toHaveBeenCalledWith(
      expect.objectContaining({ newValue: "{}" }),
    );
  });

  it("deleting the empty-object placeholder is not a data change", () => {
    const editor = mountEditor();
    fireEvent.click(screen.getByRole("button", { name: "Delete root field" }));
    expect(JSON.parse(editor.ref.current.getValue())).toEqual({});
    apply();
    expect(editor.onCellValueChanged).not.toHaveBeenCalled();
  });
});
