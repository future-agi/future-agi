import React, { useId, useMemo, useState } from "react";
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

const KINDS = [
  { value: "all", label: "All" },
  { value: "spend", label: "Spend" },
  { value: "topup", label: "Top-up" },
  { value: "grant", label: "Grant" },
  { value: "refund", label: "Refund" },
  { value: "adjustment", label: "Adjustment" },
  { value: "expiry", label: "Expiry" },
];
const KIND_TONE = {
  spend: "neutral",
  topup: "healthy",
  grant: "healthy",
  refund: "pending",
  adjustment: "warning",
  expiry: "error",
};
const kindLabel = (kind) =>
  KINDS.find((option) => option.value === kind)?.label || kind;

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

const COLUMNS = [
  {
    field: "occurred_at",
    headerName: "Time",
    minWidth: 170,
    valueFormatter: (p) => time(p.value),
    sortable: false,
  },
  {
    field: "kind",
    headerName: "Kind",
    minWidth: 130,
    cellRenderer: (p) => (
      <GatewayStatusChip
        tone={KIND_TONE[p.value] || "neutral"}
        label={kindLabel(p.value)}
        showIcon={false}
      />
    ),
  },
  {
    field: "amount_usd",
    headerName: "Amount",
    minWidth: 120,
    type: "rightAligned",
    valueFormatter: (p) => formatAmountCell(p.value),
    tooltipValueGetter: (p) => amountTooltip(p.value),
  },
  { field: "model", headerName: "Model", minWidth: 200 },
  // Every charge in this ledger is a managed call: its provider is Future
  // AGI's, never the upstream the gateway bought it from.
  // A grant or top-up has none.
  {
    colId: "provider",
    headerName: "Provider",
    minWidth: 150,
    valueGetter: (p) => (p.data?.provider ? MANAGED_PROVIDER_LABEL : ""),
  },
  {
    field: "request_id",
    headerName: "Request ID",
    minWidth: 160,
    cellStyle: { fontFamily: GATEWAY_MONO },
  },
  {
    field: "markup_usd",
    headerName: "Markup",
    minWidth: 100,
    type: "rightAligned",
    valueFormatter: (p) => formatAmountCell(p.value),
    tooltipValueGetter: (p) => amountTooltip(p.value),
    hide: true,
  },
];

// Every grant, top-up, spend, refund, adjustment and expiry in the ledger,
// newest first, a page at a time; CSV of every matching entry.
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
        <GatewayGrid
          rowData={rows}
          loading={entries.isLoading}
          columnDefs={COLUMNS}
          getRowId={(p) => p.data.id}
          stateKey="billing-gateway-ledger"
          height={420}
          emptyState={{
            icon: "billing",
            title: kind ? "No entries of this kind" : "No ledger entries yet",
            description:
              "Grants, top-ups and Future AGI model spend appear here as they happen.",
          }}
        />
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
