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
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

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
        <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
          {presentation?.title || "Waiting for query results"}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {presentation?.description ||
            "Results will appear here when available."}
        </Typography>
      </Box>
    );
  if (!result?.columns)
    return (
      <Box sx={{ px: 1, py: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
          Your results appear here
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ maxWidth: 440 }}
        >
          Run a query to inspect its results. Start with a count or a small time
          window.
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 1 }}
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
        >
          <ToggleButton value="table">Table</ToggleButton>
          <ToggleButton value="bar" disabled={!numeric.length}>
            Chart
          </ToggleButton>
        </ToggleButtonGroup>
        <Typography variant="caption" color="text.secondary">
          {rows.length.toLocaleString()} rows ·{" "}
          {columns.length.toLocaleString()} columns · {result.elapsed_ms} ms
        </Typography>
        {mode === "bar" && (
          <>
            <TextField
              select
              size="small"
              label="Category"
              value={xName}
              onChange={(e) => setX(e.target.value)}
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
          <Typography variant="subtitle2">
            {yName} by {xName}
          </Typography>
          {rows.length > 50 && (
            <Typography variant="caption">
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
          <TableContainer
            sx={{
              maxHeight: 440,
              border: 1,
              borderColor: "divider",
              borderRadius: 1,
            }}
          >
            <Table stickyHeader size="small" aria-label="SQL query results">
              <TableHead>
                <TableRow>
                  {columns.map((c) => (
                    <TableCell
                      key={c.name}
                      sx={{ whiteSpace: "nowrap", fontFamily: "monospace" }}
                    >
                      {c.name}
                      <Typography
                        component="div"
                        variant="caption"
                        color="text.secondary"
                      >
                        {c.type}
                      </Typography>
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.slice(page * 50, page * 50 + 50).map((row, i) => (
                  <TableRow key={page * 50 + i}>
                    {row.map((value, j) => (
                      <TableCell
                        key={columns[j].name}
                        sx={{
                          maxWidth: 480,
                          overflowWrap: "anywhere",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {value == null ? (
                          <Typography
                            component="span"
                            variant="caption"
                            color="text.secondary"
                          >
                            NULL
                          </Typography>
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
