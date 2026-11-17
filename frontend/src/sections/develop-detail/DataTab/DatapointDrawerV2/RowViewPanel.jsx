import React from "react";
import PropTypes from "prop-types";
import RowView from "./RowView";

/**
 * DataTabLayout's right slot (PRODUCT-SPEC §3.4, §7.2): the docked dataset
 * Row view for `?row=`. RowView renders `grid` once, always as the left
 * pane of its RowPanelShell, so opening a row never remounts AG Grid.
 * `suppressed`: Version history holds the slot (`?history=1`); stay closed
 * and leave `row` in the URL.
 */
const RowViewPanel = ({ grid, suppressed = false }) => (
  <RowView grid={grid} suppressed={suppressed} />
);

RowViewPanel.propTypes = {
  grid: PropTypes.node,
  suppressed: PropTypes.bool,
};

export default RowViewPanel;
