import { createElement } from "react";
import PropTypes from "prop-types";
import { CompareModelCell } from "./CompareControls";

// Retain the existing link/browse-state renderer and column dimensions. Context
// updates the checkbox without replacing cell renderer types on every selection.
export function withCompareSelection(column) {
  if (column.colId !== "name") return column;
  function SelectableModel(params) {
    return createElement(CompareModelCell, { data: params.data },
      createElement(column.cellRenderer, params));
  }
  SelectableModel.propTypes = { data: PropTypes.object };
  return { ...column, cellRenderer: SelectableModel };
}
