/* eslint-disable react/prop-types */
import React from "react";
import {
  Box,
  Card,
  CardContent,
  Stack,
  Typography,
} from "@mui/material";
import WidgetChart from "./WidgetChart";

const DEFAULT_WIDGET_HEIGHT = 320;

function computeRows(widgets) {
  const sorted = [...widgets].sort((a, b) => a.position - b.position);
  const rows = [];
  let currentRow = [];
  let rowWidth = 0;
  for (const w of sorted) {
    const width = w.width || 12;
    if (rowWidth + width > 12 && currentRow.length > 0) {
      rows.push(currentRow);
      currentRow = [{ ...w, width }];
      rowWidth = width;
    } else {
      currentRow.push({ ...w, width });
      rowWidth += width;
    }
  }
  if (currentRow.length > 0) rows.push(currentRow);
  return rows;
}

export default function DashboardReadOnlyView({ dashboard, shareToken }) {
  if (!dashboard) return null;

  const widgets = (dashboard.widgets || [])
    .slice()
    .sort((a, b) => a.position - b.position);

  const rows = computeRows(widgets);

  return (
    <Box sx={{ p: 3, overflow: "auto", flex: 1 }}>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h4" sx={{ fontSize: 28, fontWeight: 700, color: "text.primary" }}>
          {dashboard.name}
        </Typography>
        {dashboard.description && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {dashboard.description}
          </Typography>
        )}
      </Box>

      {rows.map((row, rowIdx) => {
        const rowHeight = Math.max(
          ...row.map((w) =>
            w.height && w.height > 50 ? w.height : DEFAULT_WIDGET_HEIGHT,
          ),
        );

        return (
          <Stack
            key={rowIdx}
            direction="row"
            spacing={1}
            sx={{ mb: 1, alignItems: "stretch" }}
          >
            {row.map((widget) => (
              <Box
                key={widget.id}
                sx={{
                  flex: `1 1 ${((widget.width || 12) / 12) * 100}%`,
                  maxWidth: `${((widget.width || 12) / 12) * 100}%`,
                  minWidth: 0,
                }}
              >
                <Card
                  variant="outlined"
                  sx={{
                    height: rowHeight,
                    display: "flex",
                    flexDirection: "column",
                    overflow: "hidden",
                  }}
                >
                  <CardContent
                    sx={{
                      p: 2,
                      "&:last-child": { pb: 2 },
                      flex: 1,
                      display: "flex",
                      flexDirection: "column",
                      overflow: "hidden",
                    }}
                  >
                    <Typography
                      variant="subtitle2"
                      fontWeight="fontWeightSemiBold"
                      noWrap
                      sx={{ mb: 1, color: "text.primary" }}
                    >
                      {widget.name}
                    </Typography>

                    <Box sx={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                      <WidgetChart
                        widget={widget}
                        shareToken={shareToken}
                        globalDateRange={null}
                      />
                    </Box>
                  </CardContent>
                </Card>
              </Box>
            ))}
          </Stack>
        );
      })}

      {widgets.length === 0 && (
        <Typography color="text.secondary" sx={{ textAlign: "center", mt: 8 }}>
          No widgets in this dashboard.
        </Typography>
      )}
    </Box>
  );
}