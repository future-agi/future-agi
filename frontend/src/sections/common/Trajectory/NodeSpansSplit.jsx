// A graph node's spans from both runs, side by side (D13):
// `lookup_order · Support Agent v3 · 2 visits | LangGraph support agent ·
// 1 visit`, one Observe SpanDetailPane per run with a visit pager
// `‹ Visit 1 of 2 ›`. Esc closes it (the host binds Esc).
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SpanDetailPane from "src/components/traceDetail/SpanDetailPane";
import { fmtCount } from "src/sections/common/format/fmt";
import { findEntry } from "./useTraceTree";

const spanOf = (e) => e?.observation_span ?? {};

function Side({ side, onSelectSpan }) {
  const ids = side.spanIds ?? [];
  const [visit, setVisit] = useState(0);
  useEffect(() => setVisit(0), [ids.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const entry = findEntry(side.tree, ids[visit]);
  return (
    <Stack
      sx={{ minWidth: 0, minHeight: 0, flex: 1 }}
      data-node-side={side.key}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 1.25,
          py: 0.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          borderTop: "3px solid",
          borderTopColor: side.color ?? "divider",
          minWidth: 0,
        }}
      >
        <Typography
          typography="s2"
          fontWeight="fontWeightSemiBold"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {side.label}
        </Typography>
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ whiteSpace: "nowrap" }}
        >
          {fmtCount(ids.length, "visit")}
        </Typography>
        <Box sx={{ flex: 1 }} />
        {ids.length > 1 ? (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.25}
            data-testid="visit-pager"
          >
            <IconButton
              size="small"
              aria-label="Previous visit"
              disabled={visit === 0}
              onClick={() => setVisit((v) => Math.max(0, v - 1))}
            >
              <Iconify icon="mdi:chevron-left" width={16} />
            </IconButton>
            <Typography
              typography="s3"
              sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}
            >
              {`Visit ${visit + 1} of ${ids.length}`}
            </Typography>
            <IconButton
              size="small"
              aria-label="Next visit"
              disabled={visit >= ids.length - 1}
              onClick={() => setVisit((v) => Math.min(ids.length - 1, v + 1))}
            >
              <Iconify icon="mdi:chevron-right" width={16} />
            </IconButton>
          </Stack>
        ) : null}
      </Stack>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {entry ? (
          <SpanDetailPane
            entry={entry}
            allSpans={side.tree}
            traceStartTime={spanOf(side.tree?.[0]).start_time}
            isRootSpan={false}
            traceTags={[]}
            onClose={() => {}}
            onSelectSpan={onSelectSpan}
            drawerOpen
          />
        ) : (
          <Box sx={{ p: 2 }}>
            <Typography typography="s2" color="text.secondary">
              {side.label} never ran this step on this try.
            </Typography>
          </Box>
        )}
      </Box>
    </Stack>
  );
}
Side.propTypes = {
  side: PropTypes.object.isRequired,
  onSelectSpan: PropTypes.func,
};

export default function NodeSpansSplit({
  node,
  sides,
  onClose,
  height = 320,
  layout = "below",
}) {
  if (!node) return null;
  // `side` (short screens): the split sits right of the graph, full height,
  // with the two runs stacked; each side scrolls inside.
  const side = layout === "side";
  return (
    <Stack
      data-testid="node-spans-split"
      data-layout={layout}
      sx={{
        height: side ? "100%" : height,
        minHeight: side ? 0 : 280,
        ...(side
          ? { borderLeft: "1px solid", flex: "0 0 58%", minWidth: 0 }
          : { borderTop: "1px solid" }),
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 1.25,
          py: 0.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography typography="s2" noWrap sx={{ minWidth: 0 }}>
          <Box component="span" sx={{ fontWeight: 600 }}>
            {node.name}
          </Box>
          {` · ${sides
            .map(
              (s) =>
                `${s.label} · ${fmtCount(s.spanIds?.length ?? 0, "visit")}`,
            )
            .join(" | ")}`}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <CustomTooltip show title="Close (Esc)" size="small" type="black">
          <IconButton
            size="small"
            aria-label="Close node spans"
            onClick={onClose}
          >
            <Iconify icon="mdi:close" width={16} />
          </IconButton>
        </CustomTooltip>
      </Stack>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: side ? "column" : "row",
        }}
      >
        {sides.map((s, i) => (
          <Box
            key={s.key}
            sx={{
              flex: 1,
              minWidth: 0,
              minHeight: 0,
              display: "flex",
              ...(side
                ? { borderTop: i ? "1px solid" : 0 }
                : { borderLeft: i ? "1px solid" : 0 }),
              borderColor: "divider",
            }}
          >
            <Side side={s} />
          </Box>
        ))}
      </Box>
    </Stack>
  );
}
NodeSpansSplit.propTypes = {
  node: PropTypes.shape({ name: PropTypes.string }),
  sides: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string,
      label: PropTypes.string,
      color: PropTypes.string,
      tree: PropTypes.array,
      spanIds: PropTypes.arrayOf(PropTypes.string),
    }),
  ).isRequired,
  onClose: PropTypes.func,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  layout: PropTypes.oneOf(["below", "side"]),
};
