import React from "react";
import PropTypes from "prop-types";
import { InclusivePriceCell } from "../InclusivePriceViews";

export default function PriceCell({ data, valueFormatted, markupPercent }) {
  if (data?.inclusivePricing) {
    return (
      <InclusivePriceCell model={data.model} markupPercent={markupPercent} />
    );
  }
  return valueFormatted ?? "—";
}

PriceCell.propTypes = {
  data: PropTypes.object,
  valueFormatted: PropTypes.string,
  markupPercent: PropTypes.number,
};

// A saved layout can reorder, hide or pin either price column. Never swallow
// Context/Access/etc. just because a qualified price needs extra room.
export function inclusivePriceSpan({ data, api, column }) {
  if (!data?.inclusivePricing) return 1;
  const displayed = api?.getAllDisplayedColumns?.();
  if (!displayed) return 2;
  const index = displayed.findIndex((col) => col.getColId() === "input");
  const next = displayed[index + 1];
  return index >= 0 &&
    next?.getColId() === "output" &&
    next.getPinned() === column?.getPinned()
    ? 2
    : 1;
}
