// Task compare · Inspect › Timeline (replaces R3's custom
// waterfall): Observe's SpanTreeTimeline per lane on ONE time axis
// (`axisRangeMs` = the longest lane), so `9.8 s` vs `81 s` reads true.
// Selecting a span opens Observe's SpanDetailPane beside the lanes.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import SpanTreeTimeline from "src/components/traceDetail/SpanTreeTimeline";
import SpanDetailPane from "src/components/traceDetail/SpanDetailPane";
import { findEntry } from "src/sections/common/Trajectory/useTraceTree";
import { fmtMs } from "src/sections/common/format/fmt";

const spanOf = (e) => e?.observation_span ?? {};

/** Root latency of a span tree (ms), or 0. */
export function treeDurationMs(tree) {
  const root = spanOf(tree?.[0]);
  if (Number.isFinite(root.latency_ms)) return root.latency_ms;
  const s = Date.parse(root.start_time);
  const e = Date.parse(root.end_time);
  return Number.isFinite(s) && Number.isFinite(e) ? Math.max(0, e - s) : 0;
}

/** The shared axis: the longest lane (at least 1 ms). */
export const sharedAxis = (graphs) => ({
  duration: Math.max(
    1,
    ...(graphs ?? []).map((g) => (g.tree ? treeDurationMs(g.tree) : 0)),
  ),
});

export default function LaneTimelines({ graphs }) {
  const [sel, setSel] = useState(null);
  const axis = useMemo(() => sharedAxis(graphs), [graphs]);
  const hit = useMemo(() => {
    for (const g of graphs ?? []) {
      const entry = findEntry(g.tree, sel);
      if (entry) return { entry, tree: g.tree };
    }
    return null;
  }, [graphs, sel]);
  return (
    <Stack
      direction="row"
      sx={{ height: "100%", minHeight: 0 }}
      data-testid="lane-timelines"
    >
      <Box sx={{ flex: 1, minWidth: 0, overflow: "auto" }}>
        {(graphs ?? []).map((g, i) => (
          <Box
            key={g.id}
            sx={{ borderTop: i ? "1px solid" : 0, borderColor: "divider" }}
          >
            <Stack
              direction="row"
              alignItems="center"
              gap={1}
              sx={{
                px: 1.25,
                py: 0.5,
                borderLeft: "3px solid",
                borderLeftColor: g.color ?? "divider",
                position: "sticky",
                top: 0,
                zIndex: 1,
                bgcolor: "background.paper",
              }}
            >
              <Typography
                typography="s2"
                fontWeight="fontWeightSemiBold"
                noWrap
              >
                {g.name}
              </Typography>
              <Typography
                typography="s3"
                color="text.secondary"
                sx={{
                  fontVariantNumeric: "tabular-nums",
                  whiteSpace: "nowrap",
                }}
              >
                {g.tree
                  ? `${fmtMs(treeDurationMs(g.tree))} of ${fmtMs(axis.duration)}`
                  : "No trace for this try"}
              </Typography>
            </Stack>
            {g.tree ? (
              <SpanTreeTimeline
                neutralLatency
                spans={g.tree}
                selectedSpanId={sel}
                onSelectSpan={setSel}
                axisRangeMs={axis}
              />
            ) : null}
          </Box>
        ))}
      </Box>
      {hit ? (
        <Box
          sx={{
            width: "42%",
            minWidth: 320,
            borderLeft: "1px solid",
            borderColor: "divider",
            overflow: "auto",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <SpanDetailPane
            entry={hit.entry}
            allSpans={hit.tree}
            traceStartTime={spanOf(hit.tree?.[0]).start_time}
            isRootSpan={spanOf(hit.entry).id === spanOf(hit.tree?.[0]).id}
            traceTags={[]}
            onClose={() => setSel(null)}
            onSelectSpan={setSel}
            drawerOpen
          />
        </Box>
      ) : null}
    </Stack>
  );
}

LaneTimelines.propTypes = {
  graphs: PropTypes.arrayOf(PropTypes.object),
};
