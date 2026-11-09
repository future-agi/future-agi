import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { useResponsive } from "src/hooks/use-responsive";

import { GatewayFilterBar, GatewayGrid } from "../components/frame";
import { formatMs } from "../logs/logStatus";
import { EMPTY, costTotal } from "../utils/formatters";
import { providerLabel } from "../utils/providerLabel";
import { useAnalyticsModels } from "./hooks/useAnalyticsModels";

// Models side by side over the range: one dense, sortable grid (the user
// shapes its columns; the shape is kept in this browser). The filter narrows
// the rows in place.

const num = (value) => (value == null ? EMPTY : Number(value).toLocaleString());
const pct = (value) => (value == null ? EMPTY : `${Number(value).toFixed(1)}%`);
const numeric = ({ value }) =>
  value == null || !Number.isFinite(Number(value)) ? null : Number(value);

// AG Grid reverses the comparator for descending order. Counter that reversal
// only for missing values, so they remain last while decimal strings sort numerically.
// eslint-disable-next-line react-refresh/only-export-components
export function compareModelMetric(a, b, _nodeA, _nodeB, descending) {
  const left = numeric({ value: a });
  const right = numeric({ value: b });
  if (left === null && right === null) return 0;
  if (left === null) return descending ? -1 : 1;
  if (right === null) return descending ? 1 : -1;
  return left - right;
}

// A row's cost over requests the gateway found no price for (stored at 0):
// unknown when none had one, a floor when some had none.
// An unknown sorts as missing, not as 0.
const rowCost = (value, row, of) =>
  costTotal(value, {
    unpriced: row?.unpriced_requests,
    requests: row?.request_count,
    cell: true,
    of,
  });
const costOrUnknown = (value, row) =>
  rowCost(value, row).unknown ? null : numeric({ value });

// The provider a row shows: "Future AGI managed" for a managed row, from
// its billing mode, never from the provider's name; the org's own OpenRouter
// key keeps "openrouter".
const shownProvider = (row) => providerLabel(row?.provider, row?.billing_mode);

function Clipped({ value }) {
  return (
    <Box
      component="span"
      title={value || undefined}
      sx={{
        display: "block",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
    >
      {value || EMPTY}
    </Box>
  );
}

Clipped.propTypes = { value: PropTypes.string };

// Each minimum holds its header: the label, 32 px of padding, the 24 px menu
// button and, sortable, the 24 px sort arrow (analytics/__tests__). Model
// and Provider hold their values too: "Future AGI managed"
// whole, and model ids as long as "meta-llama/llama-3.1-8b-instruct"; a
// longer one ends in an ellipsis with its tooltip. On a phone the pinned
// Model column keeps 180 px, so the measures still have room beside it.
const MODEL_MIN_WIDTH = 224;
const MODEL_MIN_WIDTH_PHONE = 180;

const columnsFor = (isPhone, cacheOff) =>
  [
    {
      field: "model",
      headerName: "Model",
      minWidth: isPhone ? MODEL_MIN_WIDTH_PHONE : MODEL_MIN_WIDTH,
      flex: 1.6,
      pinned: "left",
      cellRenderer: ({ value }) => <Clipped value={value} />,
    },
    {
      field: "provider",
      headerName: "Provider",
      minWidth: 150,
      flex: 1,
      // Sorted and shown by the label.
      valueGetter: ({ data }) => shownProvider(data),
      cellRenderer: ({ value }) => <Clipped value={value} />,
    },
    {
      field: "request_count",
      headerName: "Requests",
      minWidth: 144,
      flex: 0.7,
      sort: "desc",
      valueFormatter: ({ value }) => num(value),
    },
    {
      field: "total_tokens",
      headerName: "Tokens",
      minWidth: 130,
      flex: 0.7,
      valueFormatter: ({ value }) => num(value),
    },
    {
      field: "total_cost",
      headerName: "Cost",
      minWidth: 120,
      flex: 0.7,
      valueGetter: ({ data }) => costOrUnknown(data.total_cost, data),
      // Read from the row: an unknown cost's sort value is null.
      valueFormatter: ({ data }) => rowCost(data.total_cost, data).text,
      tooltipValueGetter: ({ data }) => rowCost(data.total_cost, data).title,
    },
    {
      field: "avg_cost_per_request",
      headerName: "Cost / request",
      minWidth: 176,
      flex: 0.8,
      valueGetter: ({ data }) => costOrUnknown(data.avg_cost_per_request, data),
      valueFormatter: ({ data }) =>
        rowCost(data.avg_cost_per_request, data, "average").text,
      tooltipValueGetter: ({ data }) =>
        rowCost(data.avg_cost_per_request, data, "average").title,
    },
    {
      field: "avg_latency_ms",
      headerName: "Avg latency",
      minWidth: 160,
      flex: 0.8,
      valueFormatter: ({ value }) => formatMs(value),
    },
    {
      field: "p95_latency_ms",
      headerName: "P95 latency",
      minWidth: 160,
      flex: 0.8,
      valueFormatter: ({ value }) => formatMs(value),
    },
    {
      field: "error_rate",
      headerName: "Error rate",
      minWidth: 144,
      flex: 0.7,
      cellRenderer: ({ value }) => (
        <Box
          component="span"
          sx={{ color: Number(value) > 0 ? "error.main" : "text.secondary" }}
        >
          {pct(value)}
        </Box>
      ),
    },
    {
      field: "cache_hit_rate",
      headerName: "Cache hits",
      // NEW: site claims review required for this header tooltip. Preserve
      // historical values; current enforcement does not change the period.
      headerTooltip: cacheOff
        ? "Response caching is off on this gateway. Cache hits reflect the selected period."
        : undefined,
      minWidth: 152,
      flex: 0.7,
      valueFormatter: ({ value }) => pct(value),
    },
    {
      field: "tokens_per_second",
      headerName: "Tokens / s",
      minWidth: 150,
      flex: 0.7,
      valueFormatter: ({ value }) =>
        value == null ? EMPTY : Number(value).toFixed(1),
    },
  ].map((column) =>
    ["model", "provider"].includes(column.field)
      ? column
      : {
          ...column,
          comparator: compareModelMetric,
          valueGetter:
            column.valueGetter ||
            (({ data }) => numeric({ value: data?.[column.field] })),
        },
  );

export default function ModelComparison({
  start,
  end,
  gatewayId,
  cacheOff = false,
}) {
  const { data, isLoading, error, refetch } = useAnalyticsModels({
    start,
    end,
    gatewayId,
  });
  const [search, setSearch] = useState("");
  const isPhone = useResponsive("down", "sm");
  const columns = useMemo(
    () => columnsFor(isPhone, cacheOff),
    [isPhone, cacheOff],
  );

  const models = useMemo(() => data?.models ?? [], [data]);
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return models;
    return models.filter((m) =>
      `${m.model} ${shownProvider(m)}`.toLowerCase().includes(q),
    );
  }, [models, search]);

  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      <GatewayFilterBar
        search={search}
        onSearchChange={setSearch}
        searchPlaceholder="Filter by model or provider"
        actions={
          !isLoading && !error ? (
            <Typography typography="s2" color="text.secondary">
              {`${shown.length} of ${models.length} models`}
            </Typography>
          ) : null
        }
      />
      {cacheOff && (
        <Typography typography="s2" color="text.secondary">
          {
            "Response caching is off on this gateway. Cache hits reflect the selected period."
          }
        </Typography>
      )}
      <GatewayGrid
        rowData={isLoading || error ? (error ? [] : undefined) : shown}
        loading={isLoading}
        columnDefs={columns}
        stateKey="gateway-analytics-models"
        // With group_by=billing_mode the API gives a row per model and
        // billing mode, which can repeat a model and its provider.
        getRowId={({ data }) =>
          `${data.provider}/${data.model}/${data.billing_mode ?? ""}`
        }
        height={Math.min(560, 48 + Math.max(shown.length, 4) * 36)}
        rowHeight={36}
        headerHeight={36}
        emptyState={
          error
            ? {
                icon: "alertCircle",
                title: "Models could not be loaded",
                description: error.message || "The gateway API did not answer.",
                actionLabel: "Retry",
                onAction: () => refetch(),
              }
            : search && models.length
              ? {
                  variant: "no-results",
                  title: "No matching models",
                  description: "No model in this range matches the filter.",
                  actionLabel: "Clear filter",
                  onAction: () => setSearch(""),
                }
              : {
                  icon: "analytics",
                  title: "No model traffic in this range",
                  description:
                    "Models appear here once requests go through the gateway.",
                }
        }
      />
    </Stack>
  );
}

ModelComparison.propTypes = {
  start: PropTypes.string,
  end: PropTypes.string,
  granularity: PropTypes.string,
  gatewayId: PropTypes.string,
  cacheOff: PropTypes.bool,
};
