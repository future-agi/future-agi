import React from "react";

import { formatPerMillion, formatPercent } from "../catalog";
import { FACT_CELL, FACT_NUMBER, FactText } from "./Section";

// The model page's Pricing table: per-1M-token prices from the provider and,
// when managed credits can buy the model, the managed price. The minimum
// widths fit a 390 px screen (322 px of card), so the managed price is never
// off screen; a long header or label wraps instead of being cut.
const WRAP_HEADER = { wrapHeaderText: true, autoHeaderHeight: true };
const price = { ...FACT_NUMBER, ...WRAP_HEADER, flex: 1, minWidth: 96 };

export function pricingColumns(
  sold,
  markupPercent,
  { inclusive = false } = {},
) {
  const formatPrice = ({ value }) =>
    inclusive ? value ?? "—" : formatPerMillion(value);
  const exactCell = inclusive
    ? {
        wrapText: true,
        autoHeight: true,
        cellStyle: {
          ...price.cellStyle,
          whiteSpace: "normal",
          overflowWrap: "anywhere",
          lineHeight: 1.4,
          paddingTop: 8,
          paddingBottom: 8,
        },
      }
    : {};
  const cols = [
    {
      field: "label",
      headerName: inclusive ? "Billing unit" : "Per 1M tokens",
      flex: 1.6,
      minWidth: 118,
      ...WRAP_HEADER,
      wrapText: true,
      autoHeight: true,
      cellStyle: {
        ...FACT_CELL,
        lineHeight: 1.4,
        paddingTop: 8,
        paddingBottom: 8,
      },
    },
    {
      field: "provider",
      headerName: "Provider price",
      valueFormatter: formatPrice,
      cellRenderer: ({ valueFormatted }) => (
        <FactText secondary>{valueFormatted}</FactText>
      ),
      ...price,
      ...exactCell,
    },
  ];
  if (sold) {
    cols.push({
      field: "managed",
      headerName: `Future AGI price (provider + ${formatPercent(markupPercent)})`,
      valueFormatter: formatPrice,
      cellRenderer: ({ valueFormatted }) => (
        <FactText strong>{valueFormatted}</FactText>
      ),
      ...price,
      ...exactCell,
    });
  }
  return cols;
}
