/* eslint-disable react/prop-types */
import React, { useMemo } from "react";
import { Box, Card, CardContent, Typography } from "@mui/material";
import WidgetChart from "src/sections/dashboards/WidgetChart";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_WIDGET_HEIGHT = 320;

/**
 * Group a flat sorted widget list into rows based on cumulative widths.
 * Widgets in each row are normalized so their widths sum to exactly 12.
 */
function computeRows(widgets) {
  const sorted = [...(widgets || [])].sort((a, b) => a.position - b.position);
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

// ---------------------------------------------------------------------------
// WidgetCard — read-only widget card, no edit affordances
// ---------------------------------------------------------------------------

function ReadOnlyWidgetCard({ widget }) {
  const widgetHeight =
    widget.height && widget.height > 50
      ? widget.height
      : DEFAULT_WIDGET_HEIGHT;

  return (
    <Box
      data-widget-id={widget.id}
      sx={{
        flex: `1 1 ${((widget.width || 12) / 12) * 100}%`,
        maxWidth: `${((widget.width || 12) / 12) * 100}%`,
        minWidth: 0,
        px: "4px",
      }}
    >
      <Card
        variant="outlined"
        sx={{
          height: widgetHeight,
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
          {/* Header row — just the title, no drag handle or actions */}
          <Typography
            variant="subtitle2"
            fontWeight="fontWeightSemiBold"
            noWrap
            sx={{ mb: 0.5, minHeight: 24 }}
          >
            {widget.name}
          </Typography>

          {/* Chart */}
          <Box sx={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
            <WidgetChart widget={widget} />
          </Box>
        </CardContent>
      </Card>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState({ name }) {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "50vh",
        gap: 2,
      }}
    >
      <Typography variant="h6" color="text.secondary">
        {name || "Shared Dashboard"}
      </Typography>
      <Typography variant="body2" color="text.disabled">
        This dashboard has no widgets
      </Typography>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// SharedDashboardView — read-only dashboard rendered via share link
// ---------------------------------------------------------------------------

export default function SharedDashboardView({ resourceData }) {
  const widgets = resourceData?.widgets || [];
  const rows = useMemo(() => computeRows(widgets), [widgets]);

  if (!widgets || widgets.length === 0) {
    return <EmptyState name={resourceData?.name} />;
  }

  return (
    <Box sx={{ flex: 1, overflow: "auto", p: 3 }}>
      {/* Dashboard title */}
      {resourceData?.name && (
        <Typography
          variant="h5"
          fontWeight={700}
          sx={{ mb: 2, color: "text.primary" }}
        >
          {resourceData.name}
        </Typography>
      )}

      {/* Widgets grid — no DnD, no edit buttons, pure display */}
      {rows.map((row, rowIdx) => (
        <Box
          key={rowIdx}
          sx={{
            display: "flex",
            alignItems: "stretch",
            width: "100%",
            mb: 1,
          }}
        >
          {row.map((widget) => (
            <ReadOnlyWidgetCard key={widget.id} widget={widget} />
          ))}
        </Box>
      ))}
    </Box>
  );
}
