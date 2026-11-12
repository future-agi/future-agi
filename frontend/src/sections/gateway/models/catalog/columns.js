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
const isUsable = (row) =>
  ((row?.access === ACCESS.OWN_KEY || row?.access === ACCESS.BOTH) &&
    (row?.providerAccessState ?? "ready") === "ready") ||
  ((row?.access === ACCESS.MANAGED || row?.access === ACCESS.BOTH) &&
    row?.managedOn === true);

export const compareCallable = (_a, _b, nodeA, nodeB) =>
  Number(isUsable(nodeB?.data)) - Number(isUsable(nodeA?.data));

// A per-request or inclusive quote is not a comparable scalar token rate,
// even if a legacy catalog row carries a zero in its token-price fields.
const compareTokenPrice = (a, b, nodeA, nodeB, descending) => {
  const scalar = (value, node) =>
    node?.data?.inclusivePricing || node?.data?.requestPrice != null
      ? null
      : value;
  return comparePaidPrice(
    scalar(a, nodeA),
    scalar(b, nodeB),
    nodeA,
    nodeB,
    descending,
  );
};

// Preserve native modified/new-tab link behavior. Ordinary link clicks use
// the same query/scroll handoff as a row, without changing the shared cell.
const browseModelCell = (onOpenModel) => {
  // AG Grid passes the cell params object as this renderer's props.
  const BrowseModelCell = (params) =>
    createElement(
      "div",
      {
        style: { minWidth: 0, width: "100%" },
        onClickCapture: (event) => {
          if (
            event.defaultPrevented ||
            event.button !== 0 ||
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.altKey ||
            !event.target.closest?.("a")
          )
            return;
          event.preventDefault();
          event.stopPropagation();
          onOpenModel({ data: params.data });
        },
      },
      createElement(ModelCell, params),
    );
  BrowseModelCell.displayName = "BrowseModelCell";
  return BrowseModelCell;
};

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
// columns' minimum widths (800 px) fit what is left at 1280 px (805 px with a
// classic scrollbar), so the grid does not scroll sideways there or wider;
// below that GatewayGrid shades the edge the columns run past. Each minimum
// keeps its header label whole (unsorted; the default-sorted Access and
// Released also keep their arrow). A sorted Context, Input or Output header
// can be cut at its minimum, so each has a header tooltip. Type, a facet,
// starts hidden.
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
  onOpenModel,
  pinName = false,
} = {}) {
  // Prices are what a request on Future AGI credits is charged: one number,
  // never the provider's price or the margin beside it.
  return [
    {
      colId: "name",
      field: "name",
      headerName: "Model",
      cellRenderer: onOpenModel ? browseModelCell(onOpenModel) : ModelCell,
      minWidth: 196,
      // Below lg (pinName) the columns run past the grid's edge: the name
      // stays pinned in view, at a fixed width (a pinned column with flex
      // takes a share of the whole grid). null unpins it: AG Grid keeps a pin
      // a column def leaves undefined.
      ...(pinName
        ? { pinned: "left", width: 220 }
        : { pinned: null, flex: 2.4 }),
      comparator: (a, b) => String(a).localeCompare(String(b)),
    },
    {
      colId: "vendorName",
      field: "vendorName",
      headerName: "Provider",
      cellRenderer: VendorCell,
      minWidth: 104,
      // A larger share than its minimum alone would get, so provider names
      // ("Anthropic", "Scaledown") stay whole once the grid has room (1280).
      flex: 1.4,
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
      minWidth: 118,
      flex: 1.1,
    },
    {
      colId: "context",
      field: "context",
      headerName: "Context",
      headerTooltip:
        "Context window: the most tokens one request can use, prompt and reply together.",
      valueFormatter: contextText,
      comparator: compareNumberLast,
      minWidth: 74,
      flex: 0.8,
      ...numeric,
    },
    {
      colId: "input",
      field: "input",
      headerName: inclusivePrices ? "Price / billing unit" : "Input / 1M",
      headerTooltip: inclusivePrices
        ? "Prices on Future AGI credits, in each model's stated billing unit; token prices are per 1M. Inclusive models are billed from the usage the provider reports, not a sum of dimensions."
        : "Price per 1M input tokens on Future AGI credits: what a request is charged.",
      cellRenderer: (params) =>
        createElement(PriceCell, { ...params, markupPercent }),
      colSpan: inclusivePriceSpan,
      autoHeight: true,
      wrapText: true,
      valueFormatter: (p) =>
        p.data?.requestPrice != null
          ? `${formatPerMillion(p.data.requestPrice)} / request`
          : priceText(p),
      comparator: compareTokenPrice,
      // "Price / billing unit" needs the wider column.
      minWidth: inclusivePrices ? 128 : 88,
      flex: 0.9,
      ...numeric,
    },
    {
      colId: "output",
      field: "output",
      headerName: "Output / 1M",
      headerTooltip:
        "Price per 1M output tokens on Future AGI credits: what a request is charged.",
      valueFormatter: priceText,
      comparator: compareTokenPrice,
      minWidth: 94,
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
      minWidth: 126,
      flex: 0.9,
    },
  ];
}
