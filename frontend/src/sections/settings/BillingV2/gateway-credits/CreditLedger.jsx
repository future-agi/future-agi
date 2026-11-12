import React, { useId, useMemo, useRef, useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useInfiniteQuery } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";

import {
  GatewayFilterBar,
  GatewayGrid,
  GatewayStatusChip,
} from "src/sections/gateway/components/frame";

import { creditKeys, downloadCreditCsv, fetchCreditEntries } from "./api";
import { amountTooltip, formatAmountCell } from "./format";
import { GATEWAY_MONO } from "src/sections/gateway/utils/fonts";
import { MANAGED_PROVIDER_LABEL } from "src/sections/gateway/utils/providerLabel";

// Every kind the ledger can write (ManagedCreditEntry.Kind, the entries API's
// ENTRY_KINDS), in words; the Kind filter offers each of them.
const KIND_LABEL = {
  spend: "Spend",
  usage_debit: "Platform usage",
  topup: "Top-up",
  topup_reversal: "Top-up refund",
  grant: "Grant",
  refund: "Refund",
  adjustment: "Adjustment",
  expiry: "Expiry",
  dispute_hold: "Dispute hold",
  dispute_release: "Dispute release",
};
const KINDS = [
  { value: "all", label: "All" },
  ...Object.entries(KIND_LABEL).map(([value, label]) => ({ value, label })),
];
// Money taken back (a refunded top-up, a dispute's hold) is never healthy.
const KIND_TONE = {
  spend: "neutral",
  usage_debit: "neutral",
  topup: "healthy",
  topup_reversal: "warning",
  grant: "healthy",
  refund: "pending",
  adjustment: "warning",
  expiry: "error",
  dispute_hold: "error",
  dispute_release: "pending",
};
// A kind added on the server before this screen names it still reads as
// words ("promo_clawback" -> "Promo clawback"), never as its key.
const asWords = (key) => {
  const text = String(key ?? "")
    .replace(/_/g, " ")
    .trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "Other";
};
const kindLabel = (kind) =>
  Object.hasOwn(KIND_LABEL, kind) ? KIND_LABEL[kind] : asWords(kind);
const kindTone = (kind) =>
  Object.hasOwn(KIND_TONE, kind) ? KIND_TONE[kind] : "neutral";

const time = (value) =>
  value
    ? new Date(value).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "";

// Minimum widths sum to 685 px so the ledger fits a 1024-px Billing card
// (with room for a scrollbar), where every column but Model sits at its
// minimum. Time holds a time like "Oct 20, 10:10:05 AM"; Kind holds its
// longest name, "Dispute release", whole in its badge; Amount holds an exact
// signed spend like -$0.000664 in the tabular figures AG Grid gives a
// right-aligned cell (73.2 px of Inter 12 px, so any signed amount under
// $10 at the ledger's micro precision); Provider holds "Future AGI". Spare
// width goes to the model id first. Model and Request ID show whole on hover.
// A spend's Amount is the one number the organization paid: the charge on
// Future AGI credits. The margin inside it is never a column, shown or hidden.
const COLUMNS = [
  {
    field: "occurred_at",
    headerName: "Time",
    minWidth: 150,
    valueFormatter: (p) => time(p.value),
    sortable: false,
  },
  {
    field: "kind",
    headerName: "Kind",
    minWidth: 136,
    cellRenderer: (p) => (
      <GatewayStatusChip
        tone={kindTone(p.value)}
        label={kindLabel(p.value)}
        showIcon={false}
      />
    ),
    // A column the user narrows still cuts a longer name; hover shows it
    // whole, as Model and Request ID do.
    tooltipValueGetter: (p) => kindLabel(p.value),
  },
  {
    field: "amount_usd",
    headerName: "Amount",
    minWidth: 107,
    type: "rightAligned",
    valueFormatter: (p) => formatAmountCell(p.value),
    tooltipValueGetter: (p) => amountTooltip(p.value),
  },
  {
    field: "model",
    headerName: "Model",
    minWidth: 94,
    flex: 2,
    tooltipField: "model",
  },
  // Every charge in this ledger is a managed call: its provider is Future
  // AGI's, never the upstream the gateway bought it from.
  // A grant or top-up has none.
  {
    colId: "provider",
    headerName: "Provider",
    minWidth: 94,
    valueGetter: (p) => (p.data?.provider ? MANAGED_PROVIDER_LABEL : ""),
  },
  {
    field: "request_id",
    headerName: "Request ID",
    minWidth: 104,
    tooltipField: "request_id",
    cellStyle: { fontFamily: GATEWAY_MONO },
  },
];

// A short ledger sizes the grid to its rows, so one entry does not sit above
// an empty card; past this many rows (header 40 + 9 x 40 = 400 px) it scrolls
// inside a 420-px grid, so a full page does not stretch Billing.
const FIT_ROWS = 9;
const GRID_HEIGHT = 420;

// AG Grid gives an auto-height body a 150 px minimum (room for its loading
// and "no rows" overlays), which left an empty band under one to three
// entries. With rows to show, the grid is exactly as tall as they are.
const NO_MIN_BODY = {
  "& .ag-layout-auto-height .ag-center-cols-viewport, & .ag-layout-auto-height .ag-center-cols-container, & .ag-layout-auto-height .ag-center-cols-clipper":
    { minHeight: "0 !important" },
};

// Every entry in the ledger (KIND_LABEL), newest first, a page at a time;
// CSV of every matching entry.
export default function CreditLedger() {
  const titleId = useId();
  const [choice, setChoice] = useState("all");
  const kind = choice === "all" ? "" : choice;
  const entries = useInfiniteQuery({
    queryKey: creditKeys.entries(kind),
    queryFn: ({ pageParam }) => fetchCreditEntries({ kind, cursor: pageParam }),
    initialPageParam: null,
    getNextPageParam: (page) => page.next_cursor || undefined,
    retry: false,
  });
  const rows = useMemo(
    () => entries.data?.pages.flatMap((page) => page.entries) ?? undefined,
    [entries.data],
  );
  // While a newly picked Kind loads (no rows yet) the grid keeps the layout
  // it last settled on, so a long ledger does not shrink to the loading
  // overlay and grow back. The first load starts sized to its rows.
  const settledFit = useRef(true);
  if (rows) settledFit.current = rows.length <= FIT_ROWS;
  const fitsRows = settledFit.current;
  // Loading keeps AG Grid's minimum so the loading overlay keeps its room.
  const dropMinBody = fitsRows && rows?.length > 0;

  const exportCsv = () =>
    downloadCreditCsv(
      "entries",
      kind ? { kind } : {},
      "ai-gateway-credit-ledger.csv",
    ).catch(() =>
      enqueueSnackbar("The ledger could not be exported. Try again.", {
        variant: "error",
      }),
    );

  return (
    <Box
      component="section"
      aria-labelledby={titleId}
      sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      <Typography id={titleId} typography="s1" fontWeight="fontWeightMedium">
        Credit ledger
      </Typography>
      <GatewayFilterBar
        actions={
          <Button variant="outlined" size="small" onClick={exportCsv}>
            Export CSV
          </Button>
        }
      >
        <TextField
          select
          size="small"
          label="Kind"
          value={choice}
          onChange={(event) => setChoice(event.target.value)}
          sx={{ minWidth: 160 }}
        >
          {KINDS.map((option) => (
            <MenuItem key={option.value} value={option.value}>
              {option.label}
            </MenuItem>
          ))}
        </TextField>
      </GatewayFilterBar>
      {entries.isError ? (
        <Typography typography="s1" color="text.secondary" role="alert">
          The ledger could not be loaded.
        </Typography>
      ) : (
        <Box sx={dropMinBody ? NO_MIN_BODY : undefined}>
          <GatewayGrid
            rowData={rows}
            loading={entries.isLoading}
            columnDefs={COLUMNS}
            getRowId={(p) => p.data.id}
            stateKey="billing-gateway-ledger"
            height={fitsRows ? "auto" : GRID_HEIGHT}
            domLayout={fitsRows ? "autoHeight" : "normal"}
            emptyState={{
              icon: "billing",
              title: kind ? "No entries of this kind" : "No ledger entries yet",
              description:
                "Grants, top-ups and Future AGI model spend appear here as they happen.",
            }}
          />
        </Box>
      )}
      {entries.hasNextPage && (
        <Box>
          <Button
            size="small"
            variant="outlined"
            onClick={() => entries.fetchNextPage()}
            disabled={entries.isFetchingNextPage}
          >
            Load more
          </Button>
        </Box>
      )}
    </Box>
  );
}
