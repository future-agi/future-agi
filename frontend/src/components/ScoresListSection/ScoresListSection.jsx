import { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
  CircularProgress,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { useQueueItemsForSource } from "src/api/annotation-queues/annotation-queues";
import { useScoresForSource, useSpanNotes } from "src/api/scores/scores";
import { paths } from "src/routes/paths";
import { fDateTime } from "src/utils/format-time";

function formatScoreValue(labelType, value) {
  if (value == null) return "—";
  if (labelType === "star" && value?.rating != null) {
    return `★ ${value.rating}`;
  }
  if (labelType === "categorical" && value?.selected) {
    return Array.isArray(value.selected)
      ? value.selected.join(", ")
      : value.selected;
  }
  if (labelType === "numeric" && value?.value != null) {
    return String(value.value);
  }
  if (labelType === "text" && value?.text) {
    return value.text.length > 60 ? `${value.text.slice(0, 60)}…` : value.text;
  }
  if (labelType === "thumbs_up_down" && value?.value) {
    return value.value === "up" ? "👍" : "👎";
  }
  return JSON.stringify(value);
}

/**
 * Displays a table of existing scores/annotations for a given source.
 * Supports an optional secondary source to merge scores from two levels
 * (e.g. observation_span + trace).
 * @param {string} sourceType - e.g. "dataset_row", "call_execution", "trace"
 * @param {string} sourceId - the source ID
 * @param {string} [secondarySourceType] - optional second source type
 * @param {string} [secondarySourceId] - optional second source ID
 * @param {string} [title] - optional section title
 */
export default function ScoresListSection({
  sourceType,
  sourceId,
  secondarySourceType,
  secondarySourceId,
  title = "Annotations",
  renderActions,
  openQueueItemOnRowClick = false,
}) {
  const { data: scores, isLoading } = useScoresForSource(sourceType, sourceId);
  const { data: secondaryScores, isLoading: secondaryLoading } =
    useScoresForSource(secondarySourceType, secondarySourceId);
  const { data: spanNotes = [] } = useSpanNotes(
    sourceType === "observation_span" ? sourceId : null,
  );
  const queueTargetSources = useMemo(
    () =>
      openQueueItemOnRowClick
        ? [
            { sourceType, sourceId },
            { sourceType: secondarySourceType, sourceId: secondarySourceId },
          ].filter((source) => source.sourceType && source.sourceId)
        : [],
    [
      openQueueItemOnRowClick,
      secondarySourceId,
      secondarySourceType,
      sourceId,
      sourceType,
    ],
  );
  const { data: queueEntries = [] } = useQueueItemsForSource(
    queueTargetSources,
    {
      enabled: openQueueItemOnRowClick && queueTargetSources.length > 0,
    },
  );

  const rows = useMemo(() => {
    const primary = Array.isArray(scores) ? scores : [];
    const secondary = Array.isArray(secondaryScores) ? secondaryScores : [];
    const seen = new Set();
    const merged = [];
    for (const s of [...primary, ...secondary]) {
      if (!seen.has(s.id)) {
        seen.add(s.id);
        merged.push({
          id: s.id,
          labelId: s.labelId || s.label_id,
          sourceType: s.sourceType || s.source_type,
          sourceId: s.sourceId || s.source_id,
          labelName: s.labelName,
          labelType: s.labelType,
          value: s.value,
          annotatorName: s.annotatorName || s.annotatorEmail || "System",
          scoreSource: s.scoreSource,
          notes: s.notes,
          updatedAt: s.updated_at,
          queueId: s.queueId || s.queue_id,
          queueItemId: s.queueItem || s.queue_item,
        });
      }
    }
    return merged;
  }, [scores, secondaryScores]);

  const fallbackQueueTargetsByLabel = useMemo(() => {
    const byLabel = new Map();
    const bySourceAndLabel = new Map();
    for (const entry of Array.isArray(queueEntries) ? queueEntries : []) {
      const queueId = entry?.queue?.id;
      const queueItemId = entry?.item?.id;
      if (!queueId || !queueItemId) continue;
      const itemSourceType = entry.item.sourceType || entry.item.source_type;
      const itemSourceId = entry.item.sourceId || entry.item.source_id;
      for (const label of entry.labels || []) {
        const target = { queueId, queueItemId };
        if (itemSourceType && itemSourceId) {
          bySourceAndLabel.set(
            `${itemSourceType}:${itemSourceId}:${label.id}`,
            target,
          );
        }
        if (!byLabel.has(label.id)) {
          byLabel.set(label.id, target);
        }
      }
    }
    return { byLabel, bySourceAndLabel };
  }, [queueEntries]);

  const getQueueTarget = useCallback(
    (row) => {
      if (!openQueueItemOnRowClick) return null;
      if (row.queueId && row.queueItemId) {
        return { queueId: row.queueId, queueItemId: row.queueItemId };
      }
      const sourceKey =
        row.sourceType && row.sourceId && row.labelId
          ? `${row.sourceType}:${row.sourceId}:${row.labelId}`
          : null;
      if (sourceKey) {
        const sourceTarget =
          fallbackQueueTargetsByLabel.bySourceAndLabel.get(sourceKey);
        if (sourceTarget) return sourceTarget;
      }
      return fallbackQueueTargetsByLabel.byLabel.get(row.labelId) || null;
    },
    [fallbackQueueTargetsByLabel, openQueueItemOnRowClick],
  );

  const handleOpenQueueItem = useCallback(
    (row) => {
      const target = getQueueTarget(row);
      if (!target) return;
      window.open(
        `${paths.dashboard.annotations.annotate(target.queueId)}?itemId=${target.queueItemId}`,
        "_blank",
        "noopener,noreferrer",
      );
    },
    [getQueueTarget],
  );

  if (!sourceId) return null;

  if (isLoading || secondaryLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
        <CircularProgress size={20} />
      </Box>
    );
  }

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          mb: 1.5,
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          {title && (
            <>
              <Iconify
                icon="solar:clipboard-list-linear"
                width={18}
                color="text.secondary"
              />
              <Typography variant="subtitle2" fontWeight={600}>
                {title}
              </Typography>
            </>
          )}
        </Box>
        {renderActions}
      </Box>

      {rows.length === 0 ? (
        <Typography
          variant="body2"
          color="text.disabled"
          sx={{ py: 2, textAlign: "center" }}
        >
          No annotations yet.
        </Typography>
      ) : (
        <TableContainer
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
          }}
        >
          <Table size="small">
            <TableHead>
              <TableRow sx={{ bgcolor: "background.default" }}>
                <TableCell
                  sx={{
                    fontWeight: 600,
                    fontSize: 11,
                    color: "text.secondary",
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                    py: 1,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  Label
                </TableCell>
                <TableCell
                  sx={{
                    fontWeight: 600,
                    fontSize: 11,
                    color: "text.secondary",
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                    py: 1,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  Value
                </TableCell>
                <TableCell
                  sx={{
                    fontWeight: 600,
                    fontSize: 11,
                    color: "text.secondary",
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                    py: 1,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  Annotator
                </TableCell>
                <TableCell
                  sx={{
                    fontWeight: 600,
                    fontSize: 11,
                    color: "text.secondary",
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                    py: 1,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  Updated At
                </TableCell>
                <TableCell
                  sx={{
                    fontWeight: 600,
                    fontSize: 11,
                    color: "text.secondary",
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                    py: 1,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  Label Notes
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row) => (
                <TableRow
                  key={row.id}
                  onClick={() => handleOpenQueueItem(row)}
                  sx={{
                    "&:hover": { bgcolor: "action.hover" },
                    "&:last-child td": { borderBottom: 0 },
                    cursor: getQueueTarget(row) ? "pointer" : "default",
                  }}
                >
                  <TableCell sx={{ py: 1 }}>
                    <Typography
                      sx={{
                        fontSize: 13,
                        fontWeight: 500,
                        color: "text.primary",
                      }}
                    >
                      {row.labelName}
                    </Typography>
                  </TableCell>
                  <TableCell sx={{ py: 1 }}>
                    <Tooltip
                      title={row.notes || ""}
                      placement="top"
                      arrow
                      disableHoverListener={!row.notes}
                    >
                      <Chip
                        label={formatScoreValue(row.labelType, row.value)}
                        size="small"
                        sx={{
                          height: 24,
                          fontSize: 12,
                          fontWeight: 600,
                          bgcolor: (theme) =>
                            alpha(theme.palette.primary.main, 0.08),
                          color: "primary.main",
                          border: "1px solid",
                          borderColor: (theme) =>
                            alpha(theme.palette.primary.main, 0.2),
                        }}
                      />
                    </Tooltip>
                  </TableCell>
                  <TableCell sx={{ py: 1 }}>
                    <Stack direction="row" alignItems="center" spacing={0.5}>
                      <Box
                        sx={{
                          width: 20,
                          height: 20,
                          borderRadius: "50%",
                          bgcolor: "background.neutral",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          flexShrink: 0,
                        }}
                      >
                        <Iconify
                          icon={
                            row.scoreSource === "human"
                              ? "mdi:account"
                              : "mdi:api"
                          }
                          width={12}
                          color="text.secondary"
                        />
                      </Box>
                      <Typography
                        sx={{ fontSize: 12, color: "text.secondary" }}
                        noWrap
                      >
                        {row.annotatorName}
                      </Typography>
                    </Stack>
                  </TableCell>
                  <TableCell sx={{ py: 1 }}>
                    <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
                      {fDateTime(row.updatedAt)}
                    </Typography>
                  </TableCell>
                  <TableCell sx={{ py: 1 }}>
                    <Typography
                      sx={{
                        fontSize: 12,
                        color: row.notes ? "text.primary" : "text.disabled",
                      }}
                    >
                      {row.notes || "—"}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* Span Notes table — only for observation_span sources */}
      {sourceType === "observation_span" && spanNotes.length > 0 && (
        <Box sx={{ mt: 3 }}>
          <Typography variant="subtitle2" fontWeight={600} sx={{ mb: 1.5 }}>
            Span Notes
          </Typography>
          <TableContainer
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
            }}
          >
            <Table size="small">
              <TableHead>
                <TableRow sx={{ bgcolor: "background.default" }}>
                  {["Notes", "Annotator"].map((header) => (
                    <TableCell
                      key={header}
                      sx={{
                        fontWeight: 600,
                        fontSize: 11,
                        color: "text.secondary",
                        textTransform: "uppercase",
                        letterSpacing: "0.04em",
                        py: 1,
                        borderBottom: "1px solid",
                        borderColor: "divider",
                      }}
                    >
                      {header}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {spanNotes.map((note) => (
                  <TableRow
                    key={note.id}
                    sx={{
                      "&:hover": { bgcolor: "action.hover" },
                      "&:last-child td": { borderBottom: 0 },
                    }}
                  >
                    <TableCell sx={{ py: 1.5, maxWidth: 480 }}>
                      <Typography
                        sx={{
                          fontSize: 13,
                          color: "text.primary",
                          whiteSpace: "pre-wrap",
                        }}
                      >
                        {note.notes}
                      </Typography>
                    </TableCell>
                    <TableCell sx={{ py: 1.5 }}>
                      <Stack direction="row" alignItems="center" spacing={0.5}>
                        <Box
                          sx={{
                            width: 20,
                            height: 20,
                            borderRadius: "50%",
                            bgcolor: "background.neutral",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            flexShrink: 0,
                          }}
                        >
                          <Iconify
                            icon="mdi:account"
                            width={12}
                            color="text.secondary"
                          />
                        </Box>
                        <Typography
                          sx={{ fontSize: 12, color: "text.secondary" }}
                          noWrap
                        >
                          {note.annotator || "—"}
                        </Typography>
                      </Stack>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      )}
    </Box>
  );
}

ScoresListSection.propTypes = {
  sourceType: PropTypes.string.isRequired,
  sourceId: PropTypes.string.isRequired,
  secondarySourceType: PropTypes.string,
  secondarySourceId: PropTypes.string,
  title: PropTypes.string,
  renderActions: PropTypes.node,
  openQueueItemOnRowClick: PropTypes.bool,
};
