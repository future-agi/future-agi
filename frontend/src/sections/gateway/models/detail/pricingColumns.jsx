import React from "react";

import { formatPerMillion } from "../catalog";
import { FACT_CELL, FACT_NUMBER, FactText } from "./Section";

// The model page's Pricing table: one price per row, what the organization
// pays. When Future AGI credits can buy the model that is the price a request
// on credits is charged (`managed`); otherwise it is the provider's price,
// which an own key pays (`provider`). The provider's price and the margin are
// never shown beside a credits price. The minimum widths fit a 390 px screen
// (322 px of card); a long header or label wraps instead of being cut.
const WRAP_HEADER = { wrapHeaderText: true, autoHeaderHeight: true };
const price = { ...FACT_NUMBER, ...WRAP_HEADER, flex: 1, minWidth: 96 };

export function pricingColumns(sold, { inclusive = false } = {}) {
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
  return [
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
      field: sold ? "managed" : "provider",
      colId: "price",
      headerName: "Price",
      valueFormatter: formatPrice,
      cellRenderer: ({ valueFormatted }) => (
        <FactText strong={sold}>{valueFormatted}</FactText>
      ),
      ...price,
      ...exactCell,
    },
  ];
}
