import React, { useEffect, useId, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import Iconify from "src/components/iconify";
import { MetaChip } from "./queryUi";

// Result table styling follows the shared DataTable (Environments, Evals,
// Datasets): 13px Inter, muted medium-weight headers, solid row dividers and a
// hover tint. Values stay on one line; the full value is in the cell title.
const TABLE_CONTAINER_SX = {
  maxHeight: 440,
  border: 1,
  borderColor: "divider",
  borderRadius: 1,
  "& .MuiTableCell-root": {
    typography: "s2_1",
    px: 1.5,
    py: 1,
    borderBottom: "1px solid",
    borderColor: "divider",
    whiteSpace: "nowrap",
    maxWidth: 360,
    overflow: "hidden",
    textOverflow: "ellipsis",
    fontVariantNumeric: "tabular-nums",
  },
  "& .MuiTableCell-head": {
    py: 1.25,
    color: "text.secondary",
    fontWeight: "fontWeightMedium",
    bgcolor: "background.paper",
    backgroundImage: "none",
  },
  "& .MuiTableRow-root:last-of-type .MuiTableCell-body": {
    borderBottom: "none",
  },
};

const TYPE_SX = {
  ml: 0.75,
  typography: "s3",
  fontFamily: "monospace",
  color: "text.secondary",
  fontWeight: "fontWeightRegular",
};

const EMPTY_VALUE_SX = {
  typography: "s2_1",
  fontFamily: "monospace",
  color: "text.secondary",
};

// Same segmented choice as the Environments scenario editor: separate
// outlined buttons, the selected one tinted with the brand primary.
const VIEW_TOGGLE_SX = {
  gap: 0.5,
  border: 0,
  bgcolor: "transparent",
  "& .MuiToggleButtonGroup-grouped": {
    m: 0,
    height: 32,
    px: 1.25,
    gap: 0.75,
    border: "1px solid !important",
    borderColor: (t) => `${t.palette.divider} !important`,
    borderRadius: "4px !important",
    typography: "s2",
    fontWeight: "fontWeightSemiBold",
    textTransform: "none",
    color: "text.secondary",
    bgcolor: "background.paper",
    "&:hover": { bgcolor: "action.hover", color: "text.primary" },
  },
  "& .MuiToggleButtonGroup-grouped.Mui-selected": {
    color: "primary.main",
    borderColor: (t) => `${t.palette.primary.main} !important`,
    bgcolor: (t) =>
      alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.16 : 0.09),
    "&:hover": {
      bgcolor: (t) =>
        alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.22 : 0.14),
    },
  },
};

export const numericColumns = (columns, rows) =>
  columns.filter(
    (_, i) =>
      rows.some((row) => typeof row[i] === "number") &&
      rows.every(
        (row) =>
          row[i] == null ||
          (typeof row[i] === "number" && Number.isFinite(row[i])),
      ),
  );

export default function QueryResults({
  result,
  chart = {},
  onChartChange,
  presentation,
  showEditorGuidance = true,
}) {
  const theme = useTheme();
  const patternId = useId().replace(/:/g, "");
  const [page, setPage] = useState(0);
  const [mode, setMode] = useState(chart.type || "table");
  const [x, setX] = useState(chart.x || "");
  const [y, setY] = useState(chart.y || "");
  const columns = result?.columns || [];
  const rows = result?.rows || [];
  const numeric = numericColumns(columns, rows);
  const numericIndexes = new Set(
    columns.flatMap((c, i) => (numeric.includes(c) ? [i] : [])),
  );
  const xName = columns.some((c) => c.name === x) ? x : columns[0]?.name || "";
  const yName = numeric.some((c) => c.name === y) ? y : numeric[0]?.name || "";
  const xi = columns.findIndex((c) => c.name === xName);
  const yi = columns.findIndex((c) => c.name === yName);
  const data = rows
    .slice(0, 50)
    .map((row) => ({ category: String(row[xi] ?? "NULL"), value: row[yi] }));
  const ink = theme.palette.text.secondary;
  const color = theme.palette.mode === "dark" ? "#9085e9" : "#6250d6";
  const surface = theme.palette.mode === "dark" ? "#1a1a19" : "#fcfcfb";
  useEffect(() => {
    setPage(0);
  }, [result]);
  useEffect(() => {
    onChartChange?.(
      mode === "bar" && xName && yName
        ? { type: "bar", x: xName, y: yName }
        : { type: "table" },
    );
  }, [mode, xName, yName, onChartChange]);
  if (!result?.columns && (presentation || !showEditorGuidance))
    return (
      <Box sx={{ px: 1, py: 2 }}>
        <Typography
          sx={{ typography: "s1", fontWeight: "fontWeightSemiBold", mb: 0.5 }}
        >
          {presentation?.title || "Waiting for query results"}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {presentation?.description ||
            "Results will appear here when available."}
        </Typography>
      </Box>
    );
  if (!result?.columns)
    return (
      <Box sx={{ px: 1, py: 2 }}>
        <Typography
          sx={{ typography: "s1", fontWeight: "fontWeightSemiBold", mb: 0.5 }}
        >
          Your results appear here
        </Typography>
        <Typography
          sx={{ typography: "s2", color: "text.secondary", maxWidth: 440 }}
        >
          Run a query to inspect its results. Start with a count or a small time
          window.
        </Typography>
        <Typography
          sx={{
            display: "block",
            mt: 1,
            typography: "s3",
            color: "text.secondary",
          }}
        >
          Explore fields in Catalog or choose a vetted query in Examples.
        </Typography>
      </Box>
    );
  return (
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        flexWrap="wrap"
        useFlexGap
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={mode}
          onChange={(_, value) => value && setMode(value)}
          aria-label="Result view"
          sx={VIEW_TOGGLE_SX}
        >
          <ToggleButton value="table">
            <Iconify icon="solar:server-square-linear" width={16} />
            Table
          </ToggleButton>
          <ToggleButton value="bar" disabled={!numeric.length}>
            <Iconify icon="solar:chart-2-linear" width={16} />
            Chart
          </ToggleButton>
        </ToggleButtonGroup>
        <Stack
          direction="row"
          gap={0.75}
          flexWrap="wrap"
          useFlexGap
          role="group"
          aria-label="Result details"
        >
          <MetaChip icon="solar:hamburger-menu-linear">
            {rows.length.toLocaleString()} rows
          </MetaChip>
          <MetaChip icon="solar:align-vertical-spacing-linear">
            {columns.length.toLocaleString()} columns
          </MetaChip>
          <MetaChip icon="solar:stopwatch-linear">
            {result.elapsed_ms} ms
          </MetaChip>
        </Stack>
        {mode === "bar" && (
          <>
            <TextField
              select
              size="small"
              label="Category"
              value={xName}
              onChange={(e) => setX(e.target.value)}
              InputProps={{ sx: { typography: "s2" } }}
              sx={{ minWidth: 140 }}
            >
              {columns.map((c) => (
                <MenuItem key={c.name} value={c.name}>
                  {c.name}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Value"
              value={yName}
              onChange={(e) => setY(e.target.value)}
              InputProps={{ sx: { typography: "s2" } }}
              sx={{ minWidth: 140 }}
            >
              {numeric.map((c) => (
                <MenuItem key={c.name} value={c.name}>
                  {c.name}
                </MenuItem>
              ))}
            </TextField>
          </>
        )}
      </Stack>
      {result.truncated && (
        <Alert severity="warning">
          Showing the first 10,000 rows. Downloads contain these same rows;
          aggregate or narrow your query for a complete result.
        </Alert>
      )}
      {rows.length === 0 ? (
        <Alert severity="info">
          No rows in this window. Try a wider window or check your filters.
        </Alert>
      ) : mode === "bar" ? (
        <>
          <Typography
            sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}
          >
            {yName} by {xName}
          </Typography>
          {rows.length > 50 && (
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              Chart shows the first 50 result rows. The table and downloads
              contain all returned rows.
            </Typography>
          )}
          <Box
            sx={{
              height: 300,
              bgcolor: surface,
              "@media (forced-colors: active)": {
                "& .recharts-bar-rectangle path": {
                  fill: `url(#${patternId})`,
                },
              },
            }}
            role="img"
            aria-label={`${yName} by ${xName}. Exact values are available in Table view.`}
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={data}
                accessibilityLayer
                margin={{ top: 12, right: 20, bottom: 20, left: 8 }}
              >
                <defs>
                  <pattern
                    id={patternId}
                    patternUnits="userSpaceOnUse"
                    width="6"
                    height="6"
                  >
                    <path d="M0 6L6 0" stroke="currentColor" />
                  </pattern>
                </defs>
                <CartesianGrid
                  vertical={false}
                  stroke={theme.palette.divider}
                />
                <XAxis
                  dataKey="category"
                  tick={{ fill: ink, fontSize: 11 }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: ink, fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  cursor={false}
                  contentStyle={{
                    background: theme.palette.background.paper,
                    color: theme.palette.text.primary,
                    borderColor: theme.palette.divider,
                  }}
                  itemStyle={{ color: theme.palette.text.primary }}
                />
                <Bar
                  dataKey="value"
                  name={yName}
                  fill={color}
                  maxBarSize={24}
                  radius={[4, 4, 0, 0]}
                  isAnimationActive={false}
                />
              </BarChart>
            </ResponsiveContainer>
          </Box>
        </>
      ) : (
        <>
          <TableContainer sx={TABLE_CONTAINER_SX}>
            <Table stickyHeader size="small" aria-label="SQL query results">
              <TableHead>
                <TableRow>
                  {columns.map((c, j) => (
                    <TableCell
                      key={c.name}
                      align={numericIndexes.has(j) ? "right" : "left"}
                      title={`${c.name} · ${c.type}`}
                    >
                      {c.name}
                      <Box component="span" sx={TYPE_SX}>
                        {c.type}
                      </Box>
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.slice(page * 50, page * 50 + 50).map((row, i) => (
                  <TableRow key={page * 50 + i} hover>
                    {row.map((value, j) => (
                      <TableCell
                        key={columns[j].name}
                        align={numericIndexes.has(j) ? "right" : "left"}
                        // Only long values can be cut off by the ellipsis.
                        title={
                          value != null && String(value).length > 40
                            ? String(value)
                            : undefined
                        }
                      >
                        {value == null ? (
                          <Box component="span" sx={EMPTY_VALUE_SX}>
                            NULL
                          </Box>
                        ) : value === "" ? (
                          <Box
                            component="span"
                            title="Empty string"
                            aria-label="Empty string"
                            sx={EMPTY_VALUE_SX}
                          >
                            &apos;&apos;
                          </Box>
                        ) : (
                          String(value)
                        )}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <TablePagination
            component="div"
            count={rows.length}
            rowsPerPage={50}
            rowsPerPageOptions={[50]}
            page={page}
            onPageChange={(_, value) => setPage(value)}
            sx={{
              mt: "0 !important",
              "& .MuiTablePagination-toolbar": { minHeight: 40, height: 40 },
              "& .MuiTablePagination-displayedRows": {
                typography: "s2",
                color: "text.secondary",
              },
            }}
          />
        </>
      )}
    </Stack>
  );
}
QueryResults.propTypes = {
  result: PropTypes.object,
  chart: PropTypes.object,
  onChartChange: PropTypes.func,
  presentation: PropTypes.shape({
    title: PropTypes.string.isRequired,
    description: PropTypes.string.isRequired,
  }),
  showEditorGuidance: PropTypes.bool,
};
