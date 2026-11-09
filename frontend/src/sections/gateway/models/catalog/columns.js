import { createElement } from "react";
import { formatDate } from "../../utils/formatters";
import PriceCell, { inclusivePriceSpan } from "./PriceCell";
import { CAPABILITIES, formatPerMillion, formatTokenCount } from "../catalog";
import { ACCESS, comparePaidPrice, compareNumberLast } from "../catalogRows";
import { AccessCell, ModelCell, VendorCell } from "./cells";

const DASH = "—";

export const priceText = ({ value }) =>
  value == null ? DASH : formatPerMillion(value);

const contextText = ({ value }) =>
  value == null ? DASH : formatTokenCount(value);

const releasedText = ({ value }) =>
  value ? formatDate(new Date(value * 1000).toISOString()) : DASH;

const capabilityText = ({ data }) =>
  CAPABILITIES.filter((c) => data?.capabilities?.includes(c.key))
    .map((c) => c.label)
    .join(", ") || DASH;

// Access sorts in two groups: what this organization can call, then what it
// cannot. The default sort is that, then Released, so the first
// screen leads with callable models, newest first.
export const compareCallable = (_a, _b, nodeA, nodeB) =>
  (nodeA?.data?.access === ACCESS.NONE ? 1 : 0) -
  (nodeB?.data?.access === ACCESS.NONE ? 1 : 0);

// Two-line model cells make the rows tall; every cell centres its content.
export const CENTRED = { display: "flex", alignItems: "center" };

const numeric = {
  type: "rightAligned",
  cellStyle: {
    ...CENTRED,
    justifyContent: "flex-end",
    fontVariantNumeric: "tabular-nums",
  },
};

// The facet panel beside the grid at md and up: 208 px and a 16 px gap. The
// columns' minimum widths fit what is left at 1440 px (965 px), so the grid
// does not scroll sideways; Type, a facet, starts hidden.
export const FACET_PANEL_WIDTH = 208;
export const FACET_PANEL_PX = FACET_PANEL_WIDTH + 16;

// Where the user's catalog layout is kept (GatewayGrid stateKey). v3: the
// facet panel took 224 px and Type starts hidden. A layout saved under v2 is
// carried over (fromV2Layout), not discarded.
export const CATALOG_LAYOUT_KEY = "gateway-models-catalog-v3";
export const CATALOG_LAYOUT_V2_KEY = "gateway-models-catalog-v2";

const V2_KEPT = ["colId", "hide", "pinned", "sort", "sortIndex"];

/**
 * A v2 catalog layout as v3 reads it: the user's column order, sort, pins,
 * shown or hidden columns, and the widths they set (a resized column is
 * saved with no flex). A flex column's v2 width was the wider grid's own
 * layout, so it takes v3's; Type takes v3's default (hidden, a facet now).
 */
export function fromV2Layout(state) {
  if (!Array.isArray(state)) return [];
  return state
    .filter((col) => col && typeof col.colId === "string")
    .map((col) => {
      const kept = {};
      V2_KEPT.forEach((key) => {
        if (key in col) kept[key] = col[key];
      });
      if (col.colId === "typeLabel") delete kept.hide;
      else if (col.flex == null && col.width != null) {
        kept.width = col.width;
        kept.flex = null;
      }
      return kept;
    });
}

export function catalogColumns({
  markupPercent,
  inclusivePrices = false,
} = {}) {
  const markup = markupPercent == null ? "the markup" : `${markupPercent}%`;
  return [
    {
      colId: "name",
      field: "name",
      headerName: "Model",
      cellRenderer: ModelCell,
      minWidth: 196,
      flex: 2.4,
      comparator: (a, b) => String(a).localeCompare(String(b)),
    },
    {
      colId: "vendorName",
      field: "vendorName",
      headerName: "Provider",
      cellRenderer: VendorCell,
      minWidth: 128,
      flex: 1,
    },
    {
      colId: "typeLabel",
      field: "typeLabel",
      headerName: "Type",
      // A facet beside the grid; shown from the column menu (a right click
      // on any header: GatewayGrid hides the header menu button).
      hide: true,
      minWidth: 110,
      flex: 0.8,
    },
    {
      colId: "accessLabel",
      field: "accessLabel",
      headerName: "Access",
      headerTooltip: "Sorts the models you can call first.",
      cellRenderer: AccessCell,
      comparator: compareCallable,
      sort: "asc",
      sortIndex: 0,
      minWidth: 140,
      flex: 1.1,
    },
    {
      colId: "context",
      field: "context",
      headerName: "Context",
      valueFormatter: contextText,
      comparator: compareNumberLast,
      minWidth: 108,
      flex: 0.8,
      ...numeric,
    },
    {
      colId: "input",
      field: "input",
      headerName: inclusivePrices ? "Price / billing unit" : "Input / 1M",
      headerTooltip: inclusivePrices
        ? `Future AGI prices use the stated billing unit; token prices are per 1M. Inclusive billing uses the provider's reported total + ${markup}, not a sum of dimensions.`
        : `Future AGI price per 1M input tokens: the provider's price + ${markup}.`,
      cellRenderer: (params) =>
        createElement(PriceCell, { ...params, markupPercent }),
      colSpan: inclusivePriceSpan,
      autoHeight: true,
      wrapText: true,
      valueFormatter: (p) =>
        p.data?.requestPrice != null
          ? `${formatPerMillion(p.data.requestPrice)} / request`
          : priceText(p),
      comparator: comparePaidPrice,
      minWidth: 128,
      flex: 0.9,
      ...numeric,
    },
    {
      colId: "output",
      field: "output",
      headerName: "Output / 1M",
      headerTooltip: `Future AGI price per 1M output tokens: the provider's price + ${markup}.`,
      valueFormatter: priceText,
      comparator: comparePaidPrice,
      minWidth: 136,
      flex: 0.9,
      ...numeric,
    },
    {
      colId: "maxOutput",
      field: "maxOutput",
      headerName: "Max output",
      valueFormatter: contextText,
      comparator: compareNumberLast,
      hide: true,
      minWidth: 110,
      flex: 0.8,
      ...numeric,
    },
    {
      colId: "capabilities",
      field: "capabilities",
      headerName: "Capabilities",
      valueGetter: capabilityText,
      sortable: false,
      hide: true,
      minWidth: 160,
      flex: 1.3,
    },
    {
      colId: "released",
      field: "released",
      headerName: "Released",
      valueFormatter: releasedText,
      comparator: compareNumberLast,
      sort: "desc",
      sortIndex: 1,
      minWidth: 128,
      flex: 0.9,
    },
  ];
}
