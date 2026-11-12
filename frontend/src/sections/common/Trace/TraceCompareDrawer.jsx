import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Drawer,
  IconButton,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { copyToClipboard } from "src/utils/utils";
import { fDateTime } from "src/utils/format-time";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { useGetProjectDetails } from "src/api/project/project-detail";
import { openAddData } from "src/sections/common/AddData/addDataStore";
import { runColorAt } from "src/sections/common/Runs/runColors";
import { traceRoot } from "src/sections/experiment-detail/results/drawer/alignSteps";
import TrajectoryPair from "./TrajectoryPair";

const WIDTH = 1000;

/** `chat-1052` (the session) or the trace's short id: the sentence label. */
export function traceShortLabel(trace, traceId) {
  const attrs = traceRoot(trace)?.span_attributes ?? {};
  return (
    attrs["session.id"] ??
    trace?.trace?.name ??
    String(traceId ?? "").slice(0, 8)
  );
}

/** `chat-1052 · turn 2` or the trace name. */
export function traceTitle(trace, traceId) {
  const attrs = traceRoot(trace)?.span_attributes ?? {};
  if (attrs["session.id"])
    return attrs.turn != null
      ? `${attrs["session.id"]} · turn ${attrs.turn}`
      : attrs["session.id"];
  return trace?.trace?.name ?? String(traceId ?? "").slice(0, 8);
}

function TraceHead({ traceId, color }) {
  const q = useGetTraceDetail(traceId ?? null);
  const root = traceRoot(q.data);
  const ok = String(root?.status ?? "").toUpperCase() !== "ERROR";
  if (q.isLoading) return <Skeleton variant="rounded" height={40} />;
  return (
    <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
      <Box
        sx={{
          width: 3,
          height: 32,
          borderRadius: 1,
          bgcolor: color,
          flexShrink: 0,
        }}
      />
      <Box sx={{ minWidth: 0 }}>
        <Stack direction="row" alignItems="center" gap={0.75}>
          <Typography typography="s1" fontWeight="fontWeightSemiBold" noWrap>
            {q.isError ? "Trace not found" : traceTitle(q.data, traceId)}
          </Typography>
          {!q.isError && (
            <Box
              component="span"
              sx={(t) => ({
                px: 0.75,
                height: 20,
                lineHeight: "20px",
                borderRadius: 0.5,
                typography: "s3",
                fontWeight: 500,
                color:
                  t.palette.mode === "dark"
                    ? t.palette[ok ? "success" : "error"].light
                    : t.palette[ok ? "success" : "error"].dark,
                bgcolor: alpha(
                  ok ? t.palette.success.main : t.palette.error.main,
                  0.12,
                ),
              })}
            >
              {ok ? "OK" : "Error"}
            </Box>
          )}
        </Stack>
        <Stack direction="row" alignItems="center" gap={0.5}>
          <Typography typography="s3" color="text.secondary" noWrap>
            {root?.start_time
              ? fDateTime(root.start_time, "dd MMM yyyy, HH:mm:ss")
              : "—"}
          </Typography>
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title={`Copy trace id ${traceId}`}
          >
            <IconButton
              size="small"
              aria-label="Copy trace id"
              onClick={() => {
                copyToClipboard(traceId);
                enqueueSnackbar("Trace id copied", { variant: "success" });
              }}
              sx={{ p: 0.25, color: "text.secondary" }}
            >
              <Iconify icon="basil:copy-outline" width={13} />
            </IconButton>
          </CustomTooltip>
        </Stack>
      </Box>
    </Stack>
  );
}

TraceHead.propTypes = { traceId: PropTypes.string, color: PropTypes.string };

/**
 * Two traces side by side, aligned step by step (R2-SPEC §3.3.6). Mounted by
 * the Observe traces page; opens when `compareTraces=<a>,<b>` names two
 * traces (readR2Params / compareTracesHref). `Esc` closes it (and the page
 * drops the param).
 *
 * Props (frozen, R2 manifest row 24):
 * @param {boolean} props.open
 * @param {Function} props.onClose
 * @param {string} props.leftTraceId
 * @param {string} props.rightTraceId
 */
const TraceCompareDrawer = ({ open, onClose, leftTraceId, rightTraceId }) => {
  const qa = useGetTraceDetail(open ? leftTraceId ?? null : null);
  const qb = useGetTraceDetail(open ? rightTraceId ?? null : null);
  const projectId =
    qa.data?.trace?.project_id ?? qb.data?.trace?.project_id ?? null;
  const { data: project } = useGetProjectDetails(
    projectId,
    Boolean(open && projectId),
  );
  const projectName = project?.name ?? project?.project?.name ?? null;
  const labelA = traceShortLabel(qa.data, leftTraceId);
  let labelB = traceShortLabel(qb.data, rightTraceId);
  if (labelB === labelA) labelB = `${labelB} (2)`;
  const colors = [runColorAt(0).solid, runColorAt(1).solid];

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: `min(${WIDTH}px, 100vw)`,
          display: "flex",
          flexDirection: "column",
        },
      }}
      data-testid="trace-compare-drawer"
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 2,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon="mdi:compare-horizontal"
          width={20}
          sx={{ color: "text.secondary" }}
        />
        <Typography
          typography="m3"
          fontWeight="fontWeightSemiBold"
          sx={{ flex: 1 }}
          noWrap
        >
          {`Compare traces${projectName ? ` · ${projectName}` : ""}`}
        </Typography>
        <CustomTooltip
          show
          size="small"
          placement="bottom"
          arrow
          title="Close (Esc)"
        >
          <IconButton aria-label="Close" onClick={onClose} size="small">
            <Iconify icon="mingcute:close-line" width={20} />
          </IconButton>
        </CustomTooltip>
      </Stack>
      <Box
        sx={{
          px: 2,
          py: 1.25,
          display: "grid",
          gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)",
          gap: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <TraceHead traceId={leftTraceId} color={colors[0]} />
        <TraceHead traceId={rightTraceId} color={colors[1]} />
      </Box>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, py: 1.5 }}>
        {open && leftTraceId && rightTraceId ? (
          <TrajectoryPair
            left={{ traceId: leftTraceId, label: labelA }}
            right={{ traceId: rightTraceId, label: labelB }}
          />
        ) : null}
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ px: 2, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button
          variant="contained"
          size="small"
          startIcon={<Iconify icon="mdi:folder-move-outline" width={16} />}
          onClick={() =>
            openAddData({
              source: {
                kind: "traces",
                trace_ids: [leftTraceId, rightTraceId].filter(Boolean),
                project_id: projectId,
              },
            })
          }
        >
          Add both to a dataset…
        </Button>
        <Box sx={{ flex: 1 }} />
        <Button
          variant="outlined"
          size="small"
          startIcon={<Iconify icon="mdi:link-variant" width={16} />}
          onClick={() => {
            copyToClipboard(window.location.href);
            enqueueSnackbar("Link copied", { variant: "success" });
          }}
        >
          Copy link
        </Button>
      </Stack>
    </Drawer>
  );
};

TraceCompareDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  leftTraceId: PropTypes.string,
  rightTraceId: PropTypes.string,
};

export default TraceCompareDrawer;
