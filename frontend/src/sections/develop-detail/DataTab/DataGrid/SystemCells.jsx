import React from "react";
import PropTypes from "prop-types";
import { alpha, Box, Chip, IconButton, Typography } from "@mui/material";
import { useLocation, useNavigate } from "react-router";
import { enqueueSnackbar } from "notistack";
import { formatDistanceToNowStrict } from "date-fns";
import { create } from "zustand";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SplitChips from "src/sections/common/Datasets/SplitChips";
import { useDatasetSplits } from "src/api/develop/dataset-splits";
import { ROW_STATUS_LABEL, useChangeSet } from "../Versions/changeSetStore";
import { effectiveSplitIds } from "./dataGridView";
import RowIssuesGlyph from "src/sections/common/Values/RowIssuesGlyph";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { VOCAB } from "src/sections/common/vocab";
import { copyToClipboard } from "src/utils/utils";
import {
  isExpiredSource,
  rowKindOf,
  sourceChip,
} from "src/sections/develop-detail/TaskSuite/itemModel";
import { shownRowSource } from "../DatapointDrawerV2/rowSource";

// Cell renderers of the versioned grid's system columns (area-datasets G1, G2):
// Row, Splits, Source, Updated. Each reads AG Grid's `data` (a table row with
// row_meta) and `context.datasetId`; pending changes come from the change-set.

/** Opens TraceDetailDrawerV2 from a Source cell (mounted once by the grid). */
export const useSourceTraceStore = create((set) => ({
  trace: null, // {traceId, projectId, spanId}
  open: (trace) => set({ trace }),
  close: () => set({ trace: null }),
}));

const cellBox = {
  display: "flex",
  alignItems: "center",
  height: "100%",
  px: 1,
  minWidth: 0,
};

const datasetIdOf = (props) => props?.datasetId ?? props?.context?.datasetId;

/**
 * R2 (P5): `Task` / `Conversation` kind badge of a row (the row's `kind`,
 * else the dataset's). Conversations show the icon only (the Row column is
 * 72 px); the tooltip names the kind.
 */
export const RowKindBadge = ({ kind }) => {
  if (kind !== "task" && kind !== "conversation") return null;
  const label = kind === "task" ? VOCAB.taskBadge : VOCAB.conversationBadge;
  return (
    <CustomTooltip show title={label} placement="top" size="small" arrow>
      <Box
        component="span"
        aria-label={label}
        data-testid="row-kind-badge"
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.25,
          height: 18,
          px: kind === "task" ? 0.5 : 0.375,
          borderRadius: 0.5,
          flexShrink: 0,
          typography: "s3",
          fontWeight: 600,
          bgcolor: (t) => alpha(t.palette.primary.main, 0.12),
          color: "primary.main",
        }}
      >
        {kind === "task" ? (
          label
        ) : (
          <Iconify icon="mdi:forum-outline" width={12} />
        )}
      </Box>
    </CustomTooltip>
  );
};
RowKindBadge.propTypes = { kind: PropTypes.string };

/** G1: `order+1`, `New` for a pending row, struck through + restore for a pending delete. */
// W3-3: a row that is not plain Approved shows its status (pending first).
const STATUS_ICON = {
  needs_review: { icon: "mdi:account-clock-outline", color: "warning.main" },
  draft: { icon: "mdi:pencil-circle-outline", color: "text.secondary" },
  archived: { icon: "mdi:archive-outline", color: "text.disabled" },
  approved: { icon: "mdi:check-decagram-outline", color: "success.main" },
};
export const RowStatusGlyph = ({ status, reason, pending }) => {
  if (!status || (status === "approved" && !pending)) return null;
  const s = STATUS_ICON[status] ?? STATUS_ICON.draft;
  const label = `${ROW_STATUS_LABEL[status] ?? status}${reason ? `: ${reason}` : ""}${pending ? " (unsaved)" : ""}`;
  return (
    <CustomTooltip show title={label} placement="top" size="small" arrow>
      <Box
        component="span"
        aria-label={label}
        data-testid={`row-status-${status}`}
        sx={{ display: "inline-flex", alignItems: "center" }}
      >
        <Iconify icon={s.icon} width={14} sx={{ color: s.color }} />
      </Box>
    </CustomTooltip>
  );
};
RowStatusGlyph.propTypes = {
  status: PropTypes.string,
  reason: PropTypes.string,
  pending: PropTypes.bool,
};

export const RowNumberCell = (props) => {
  const datasetId = datasetIdOf(props);
  const { deletedRowIds, revert, pendingStatus } = useChangeSet(datasetId);
  const { kind: datasetKind } = useDatasetKind(datasetId);
  const data = props.data;
  if (!data) return null;
  if (data.__pending === "new") {
    return (
      <Box sx={cellBox}>
        <Chip
          label="New"
          size="small"
          sx={{
            height: 20,
            typography: "s3",
            fontWeight: 600,
            bgcolor: (t) => alpha(t.palette.warning.main, 0.16),
            color: (t) =>
              t.palette.mode === "dark"
                ? t.palette.warning.light
                : t.palette.warning.dark,
            "& .MuiChip-label": { px: 0.75 },
          }}
        />
      </Box>
    );
  }
  const n = (data.order ?? 0) + 1;
  const deleted = deletedRowIds.includes(data.row_id);
  return (
    <Box sx={{ ...cellBox, gap: 0.5, justifyContent: "flex-start" }}>
      <Typography
        typography="s2"
        sx={{
          fontVariantNumeric: "tabular-nums",
          color: deleted ? "text.disabled" : "text.secondary",
          textDecoration: deleted ? "line-through" : "none",
        }}
      >
        {n}
      </Typography>
      <RowKindBadge kind={rowKindOf(data, datasetKind)} />
      <RowIssuesGlyph rowMeta={data?.row_meta} />
      <RowStatusGlyph
        status={pendingStatus?.[data.row_id]?.status ?? data?.row_meta?.status}
        reason={
          pendingStatus?.[data.row_id]?.reason ?? data?.row_meta?.status_reason
        }
        pending={Boolean(pendingStatus?.[data.row_id])}
      />
      {deleted && (
        <CustomTooltip
          show
          title="Restore row"
          placement="top"
          size="small"
          arrow
        >
          <IconButton
            size="small"
            aria-label="Restore row"
            onClick={(e) => {
              e.stopPropagation();
              revert(data.row_id);
            }}
            sx={{ p: 0.25, ml: "auto" }}
          >
            <Iconify
              icon="mdi:restore"
              width={14}
              sx={{ color: "warning.main" }}
            />
          </IconButton>
        </CustomTooltip>
      )}
    </Box>
  );
};

/** G2: split chips (max 2), with pending split changes applied. */
export const SplitsCell = (props) => {
  const datasetId = datasetIdOf(props);
  const { data: splitData } = useDatasetSplits(datasetId);
  const { ops } = useChangeSet(datasetId);
  const data = props.data;
  if (!data) return null;
  const base = data.row_meta?.split_ids ?? [];
  const ids = effectiveSplitIds(base, ops, data.row_id);
  const changed = ids.join() !== base.join();
  const splits = ids.map((id) => splitData?.byId?.[id]).filter(Boolean);
  return (
    <Box
      sx={{
        ...cellBox,
        ...(changed && {
          bgcolor: (t) => alpha(t.palette.warning.main, 0.16),
          boxShadow: (t) => `inset 3px 0 0 ${t.palette.warning.main}`,
        }),
      }}
    >
      {splits.length ? (
        <SplitChips splits={splits} max={2} />
      ) : (
        <Typography typography="s3" color="text.disabled">
          No split
        </Typography>
      )}
    </Box>
  );
};

/**
 * G2 / §8.3 + R2 §7.3.6: where the row came from — `Session chat-1052 ↗`,
 * `Trace 7e1… ↗`, `File support-golden-sep.csv · line 37`, `#2D result`.
 * Sessions and expired traces open the copy saved at import (Row view
 * Conversation / Trajectory tab); fresh traces open the trace drawer.
 * Ellipsis + tooltip with the full id; a copy button shows on hover.
 */
export const SourceCell = (props) => {
  const navigate = useNavigate();
  const location = useLocation();
  const openTrace = useSourceTraceStore((s) => s.open);
  const data = props.data;
  if (!data) return null;
  // IT-R3 G5 P1: an imported pending row keeps its session / trace source.
  // A build that hides the row source surface shows no import source.
  const source =
    data.__pending === "new"
      ? shownRowSource(data.row_meta?.source) ?? { type: "manual" }
      : shownRowSource(data.row_meta?.source);
  const chip = sourceChip(source, { rowNumber: (data.order ?? 0) + 1 });
  const openRowTab = (tab) => {
    const q = new URLSearchParams(location.search);
    q.set("row", data.row_id);
    q.set("rowTab", tab);
    q.delete("history");
    navigate({ search: `?${q.toString()}` });
  };
  const onClick = (() => {
    if (chip.kind === "session") return () => openRowTab("conversation");
    if (chip.kind === "trace" && source?.trace_id)
      return isExpiredSource(source)
        ? () => openRowTab("trajectory")
        : () =>
            openTrace({
              traceId: source.trace_id,
              projectId: source.project_id,
              spanId: source.span_id,
            });
    if (chip.kind === "copy" && source?.dataset_id)
      return () =>
        navigate(
          `/dashboard/develop/${source.dataset_id}?tab=data${source.row_id ? `&row=${source.row_id}` : ""}`,
        );
    if (chip.kind === "experiment" && source?.experiment_id)
      return () =>
        navigate(
          `/dashboard/develop/experiment/${source.experiment_id}/data?row=${data.row_id}${source.run_id ? `&run=${source.run_id}` : ""}`,
        );
    return null;
  })();
  return (
    <Box
      sx={{
        ...cellBox,
        gap: 0.25,
        "&:hover .source-copy, & .source-copy:focus-visible": { opacity: 1 },
      }}
    >
      <CustomTooltip
        show={Boolean(chip.tooltip)}
        title={chip.tooltip}
        placement="top"
        size="small"
        arrow
      >
        <Box
          component={onClick ? "button" : "span"}
          type={onClick ? "button" : undefined}
          data-testid="source-chip"
          onClick={
            onClick
              ? (e) => {
                  e.stopPropagation();
                  onClick();
                }
              : undefined
          }
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
            minWidth: 0,
            maxWidth: "100%",
            border: 0,
            p: 0,
            bgcolor: "transparent",
            cursor: onClick ? "pointer" : "default",
            color: onClick ? "primary.main" : "text.secondary",
            "&:hover": onClick ? { textDecoration: "underline" } : undefined,
          }}
        >
          {chip.icon && (
            <Iconify icon={chip.icon} width={14} sx={{ flexShrink: 0 }} />
          )}
          <Typography typography="s2" noWrap sx={{ color: "inherit" }}>
            {chip.label}
          </Typography>
        </Box>
      </CustomTooltip>
      {chip.copy && (
        <IconButton
          size="small"
          className="source-copy"
          aria-label="Copy source id"
          onClick={(e) => {
            e.stopPropagation();
            copyToClipboard(chip.copy);
            enqueueSnackbar(`${chip.copy} copied`, { variant: "success" });
          }}
          sx={{ p: 0.25, opacity: 0, flexShrink: 0, ml: "auto" }}
        >
          <Iconify icon="mdi:content-copy" width={12} />
        </IconButton>
      )}
    </Box>
  );
};

/** G2: relative time + the version that last changed the row. */
export const UpdatedCell = (props) => {
  const data = props.data;
  if (!data || data.__pending) return null;
  const at = data.row_meta?.updated_at;
  const v = data.row_meta?.last_changed_in_version;
  let rel = "—";
  try {
    if (at) rel = formatDistanceToNowStrict(new Date(at), { addSuffix: true });
  } catch {
    rel = "—";
  }
  return (
    <Box sx={{ ...cellBox, gap: 0.75 }}>
      <CustomTooltip
        show={Boolean(at)}
        title={at ? new Date(at).toLocaleString() : ""}
        placement="top"
        size="small"
        arrow
      >
        <Typography typography="s2" color="text.secondary" noWrap>
          {rel}
        </Typography>
      </CustomTooltip>
      {v != null && (
        <Typography
          typography="s3"
          sx={{
            px: 0.5,
            borderRadius: 0.5,
            bgcolor: "action.hover",
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          v{v}
        </Typography>
      )}
    </Box>
  );
};

const cellPropTypes = {
  data: PropTypes.object,
  context: PropTypes.object,
  datasetId: PropTypes.string,
};
RowNumberCell.propTypes = cellPropTypes;
SplitsCell.propTypes = cellPropTypes;
SourceCell.propTypes = cellPropTypes;
UpdatedCell.propTypes = cellPropTypes;
