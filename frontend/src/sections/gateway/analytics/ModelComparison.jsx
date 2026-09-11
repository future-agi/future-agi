import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Card,
  Typography,
  Skeleton,
  Stack,
  TextField,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  InputAdornment,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useAnalyticsModels } from "./hooks/useAnalyticsModels";

// ---------------------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------------------

const COLUMNS = [
  {
    id: "model",
    label: "Model",
    align: "left",
    sortable: true,
    format: (v) => v || "--",
  },
  {
    id: "provider",
    label: "Provider",
    align: "left",
    sortable: true,
    format: (v) => v || "--",
  },
  {
    id: "request_count",
    label: "Requests",
    align: "right",
    sortable: true,
    format: (v) => (v != null ? Number(v).toLocaleString() : "--"),
  },
  {
    id: "avg_latency_ms",
    label: "Avg Latency",
    align: "right",
    sortable: true,
    format: (v) => {
      if (v == null) return "--";
      const n = Number(v);
      if (Number.isNaN(n)) return "--";
      if (n >= 1000) return `${(n / 1000).toFixed(2)}s`;
      return `${Math.round(n)}ms`;
    },
  },
  {
    id: "p95_latency_ms",
    label: "P95 Latency",
    align: "right",
    sortable: true,
    format: (v) => {
      if (v == null) return "--";
      const n = Number(v);
      if (Number.isNaN(n)) return "--";
      if (n >= 1000) return `${(n / 1000).toFixed(2)}s`;
      return `${Math.round(n)}ms`;
    },
  },
  {
    id: "error_rate",
    label: "Error Rate",
    align: "right",
    sortable: true,
    format: (v) => {
      if (v == null) return "--";
      const n = Number(v);
      return Number.isNaN(n) ? "--" : `${n.toFixed(2)}%`;
    },
  },
  {
    id: "total_cost",
    label: "Cost",
    align: "right",
    sortable: true,
    format: (v) => {
      if (v == null) return "--";
      const n = Number(v);
      if (Number.isNaN(n)) return "--";
      if (n >= 1000) return `$${(n / 1000).toFixed(1)}K`;
      if (n >= 1) return `$${n.toFixed(2)}`;
      return `$${n.toFixed(4)}`;
    },
  },
  {
    id: "cache_hit_rate",
    label: "Cache Hit Rate",
    align: "right",
    sortable: true,
    format: (v) => {
      if (v == null) return "--";
      const n = Number(v);
      return Number.isNaN(n) ? "--" : `${n.toFixed(1)}%`;
    },
  },
];

// ---------------------------------------------------------------------------
// Comparator
// ---------------------------------------------------------------------------

function descendingComparator(a, b, orderBy) {
  const aVal = a[orderBy];
  const bVal = b[orderBy];

  // Handle nulls — push them to the end
  if (aVal == null && bVal == null) return 0;
  if (aVal == null) return 1;
  if (bVal == null) return -1;

  if (typeof aVal === "string") {
    return bVal.localeCompare(aVal);
  }
  if (bVal < aVal) return -1;
  if (bVal > aVal) return 1;
  return 0;
}

function getComparator(order, orderBy) {
  return order === "desc"
    ? (a, b) => descendingComparator(a, b, orderBy)
    : (a, b) => -descendingComparator(a, b, orderBy);
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const ModelComparison = ({ start, end, gatewayId }) => {
  const [selectedModels, setSelectedModels] = useState("");
  const [order, setOrder] = useState("desc");
  const [orderBy, setOrderBy] = useState("request_count");

  const { data, isLoading } = useAnalyticsModels({
    start,
    end,
    models: selectedModels || undefined,
    gatewayId,
  });

  const models = useMemo(() => data?.models || [], [data?.models]);

  const sortedModels = useMemo(() => {
    const comparator = getComparator(order, orderBy);
    return [...models].sort(comparator);
  }, [models, order, orderBy]);

  const handleRequestSort = (columnId) => {
    const isAsc = orderBy === columnId && order === "asc";
    setOrder(isAsc ? "desc" : "asc");
    setOrderBy(columnId);
  };

  // ---------------------------------------------------------------------------
  // Skeleton loading
  // ---------------------------------------------------------------------------

  if (isLoading) {
    return (
      <Card sx={{ p: 3 }}>
        <Stack spacing={2}>
          <Skeleton variant="text" width={200} height={24} />
          <Skeleton
            variant="rectangular"
            height={48}
            sx={{ borderRadius: 1 }}
          />
          {[...Array(6)].map((_, i) => (
            <Skeleton
              key={i}
              variant="rectangular"
              height={44}
              sx={{ borderRadius: 1 }}
            />
          ))}
        </Stack>
      </Card>
    );
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <Box>
      {/* Filter input */}
      <Stack direction="row" alignItems="center" spacing={2} mb={2}>
        <TextField
          placeholder="Filter models (comma-separated)..."
          size="small"
          value={selectedModels}
          onChange={(e) => setSelectedModels(e.target.value)}
          sx={{ minWidth: 320 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="eva:search-outline" width={18} />
              </InputAdornment>
            ),
          }}
        />
        <Typography variant="body2" color="text.secondary">
          {sortedModels.length} model{sortedModels.length !== 1 ? "s" : ""}
        </Typography>
      </Stack>

      <Card sx={{ p: 0 }}>
        {sortedModels.length > 0 ? (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  {COLUMNS.map((col) => (
                    <TableCell
                      key={col.id}
                      align={col.align}
                      sx={{ fontWeight: 600, whiteSpace: "nowrap" }}
                    >
                      {col.sortable ? (
                        <TableSortLabel
                          active={orderBy === col.id}
                          direction={orderBy === col.id ? order : "asc"}
                          onClick={() => handleRequestSort(col.id)}
                        >
                          {col.label}
                        </TableSortLabel>
                      ) : (
                        col.label
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {sortedModels.map((row, index) => (
                  <TableRow key={row.model || index} hover>
                    {COLUMNS.map((col) => (
                      <TableCell key={col.id} align={col.align}>
                        <Typography
                          variant="body2"
                          sx={{
                            fontWeight: col.id === "model" ? 600 : 400,
                            color:
                              col.id === "error_rate" &&
                              row.error_rate != null &&
                              row.error_rate > 5
                                ? "error.main"
                                : "text.primary",
                          }}
                        >
                          {col.format(row[col.id])}
                        </Typography>
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        ) : (
          <Box
            display="flex"
            alignItems="center"
            justifyContent="center"
            py={6}
          >
            <Typography variant="body2" color="text.secondary">
              No model data available for this time range.
            </Typography>
          </Box>
        )}
      </Card>
    </Box>
  );
};

ModelComparison.propTypes = {
  start: PropTypes.string,
  end: PropTypes.string,
  gatewayId: PropTypes.string,
};

export default ModelComparison;
