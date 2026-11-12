import { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useQueueItemsForSource } from "src/api/annotation-queues/annotation-queues";
import { useScoresForSource, useSpanNotes } from "src/api/scores/scores";
import { paths } from "src/routes/paths";
import { fDateTime } from "src/utils/format-time";
import ContentSkeleton from "src/components/content-skeleton";

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

function normalizeEntityId(value) {
  if (!value) return null;
  if (typeof value === "object") return value.id || value.pk || null;
  return value;
}

// An edge of the annotations table with more columns past it fades out over
// 32 px. The gradients are written left to right; in a right-to-left layout
// the theme's RTL style plugin mirrors them.
const EDGE_FADE = 32;
const fadeMask = (...stops) => {
  const mask = `linear-gradient(${stops.join(", ")})`;
  return { WebkitMaskImage: mask, maskImage: mask };
};
const SOLID_FROM_START = `black ${EDGE_FADE}px`;
const SOLID_TO_END = `black calc(100% - ${EDGE_FADE}px)`;
const HIDDEN_EDGE_FADES = {
  '&[data-hidden-edges="end"]': fadeMask(
    "to right",
    SOLID_TO_END,
    "transparent",
  ),
  '&[data-hidden-edges="start"]': fadeMask(
    "to left",
    SOLID_TO_END,
    "transparent",
  ),
  '&[data-hidden-edges="both"]': fadeMask(
    "to right",
    "transparent",
    SOLID_FROM_START,
    SOLID_TO_END,
    "transparent",
  ),
};

// Which edges of a sideways-scrolling element have content past them:
// "start", "end", "both" or null. The scrollbar alone is no cue: macOS
// overlay scrollbars and headless browsers show it only while scrolling.
function useHiddenEdges(scroller) {
  const [edges, setEdges] = useState(null);
  useEffect(() => {
    if (!scroller) return undefined;
    const update = () => {
      // In a right-to-left layout scrollLeft counts down from 0.
      const scrolled = Math.abs(scroller.scrollLeft);
      const past = scroller.scrollWidth - scroller.clientWidth;
      const start = scrolled > 1;
      const end = past - scrolled > 1;
      setEdges(start && end ? "both" : start ? "start" : end ? "end" : null);
    };
    update();
    scroller.addEventListener("scroll", update, { passive: true });
    // A narrower pane or a longer new row changes what fits.
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(scroller);
    if (scroller.firstElementChild) {
      observer?.observe(scroller.firstElementChild);
    }
    return () => {
      scroller.removeEventListener("scroll", update);
      observer?.disconnect();
    };
  }, [scroller]);
  return edges;
}

export default function ScoresListSection({
  sourceType,
  sourceId,
  secondarySourceType,
  secondarySourceId,
  projectId,
  title = "Annotations",
  renderActions,
  openQueueItemOnRowClick = false,
  shared,
}) {
  // A restricted shared link hands in the annotations it read through the
  // link. The viewer's own session must not answer for the sharer's span
  // (another organization's viewer would see "No annotations yet"), so the
  // viewer-session reads below stay off and send no request.
  const fromLink = Boolean(shared);
  const viewerReads = fromLink ? { enabled: false } : {};
  // The same trace / span id can exist in several projects; list only the
  // scores of the copy this drawer shows.
  const { data: scores, isLoading } = useScoresForSource(sourceType, sourceId, {
    projectId,
    ...viewerReads,
  });
  const { data: secondaryScores, isLoading: secondaryLoading } =
    useScoresForSource(secondarySourceType, secondarySourceId, {
      projectId,
      ...viewerReads,
    });
  const listedScores = fromLink ? shared.scores : scores;
  const listedSecondaryScores = fromLink ? null : secondaryScores;
  const spanNotesSourceId = useMemo(
    () =>
      sourceType === "observation_span"
        ? sourceId
        : secondarySourceType === "observation_span"
          ? secondarySourceId
          : null,
    [secondarySourceId, secondarySourceType, sourceId, sourceType],
  );
  const { data: viewerSpanNotes = [] } = useSpanNotes(spanNotesSourceId, {
    projectId,
    ...viewerReads,
  });
  const spanNotes = fromLink ? shared.spanNotes || [] : viewerSpanNotes;
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
      projectId,
      enabled:
        !fromLink && openQueueItemOnRowClick && queueTargetSources.length > 0,
    },
  );

  const rows = useMemo(() => {
    const primary = Array.isArray(listedScores) ? listedScores : [];
    const secondary = Array.isArray(listedSecondaryScores)
      ? listedSecondaryScores
      : [];
    const seen = new Set();
    const merged = [];
    for (const s of [...primary, ...secondary]) {
      if (!seen.has(s.id)) {
        seen.add(s.id);
        merged.push({
          id: s.id,
          labelId: s.label_id,
          sourceType: s.source_type,
          sourceId: s.source_id,
          labelName: s.label_name || "—",
          labelType: s.label_type,
          value: s.value,
          annotatorName: s.annotator_name || s.annotator_email || "System",
          scoreSource: s.score_source,
          notes: s.notes,
          updatedAt: s.updated_at,
          queueId: normalizeEntityId(s.queue_id),
          queueItemId: normalizeEntityId(s.queue_item),
        });
      }
    }
    return merged;
  }, [listedScores, listedSecondaryScores]);

  const fallbackQueueTargetsByLabel = useMemo(() => {
    const byLabel = new Map();
    const bySourceAndLabel = new Map();
    for (const entry of Array.isArray(queueEntries) ? queueEntries : []) {
      const queueId = entry?.queue?.id;
      const queueItemId = entry?.item?.id;
      if (!queueId || !queueItemId) continue;
      const itemSourceType = entry.item.source_type;
      const itemSourceId = entry.item.source_id;
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

  // The annotations table scrolls sideways in a pane narrower than its
  // column floors; the edge with more columns past it fades out.
  const [tableScroller, setTableScroller] = useState(null);
  const hiddenEdges = useHiddenEdges(tableScroller);

  if (!sourceId) return null;

  if (!fromLink && (isLoading || secondaryLoading)) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
        <ContentSkeleton variant="rows" />
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
                width={20}
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
        // The card border sits on this wrapper, so the scroller below can
        // fade an edge without fading the border with it.
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            overflow: "hidden",
          }}
        >
          <TableContainer
            ref={setTableScroller}
            data-hidden-edges={hiddenEdges || undefined}
            sx={HIDDEN_EDGE_FADES}
          >
            <Table size="small">
              <TableHead>
                <TableRow sx={{ bgcolor: "background.default" }}>
                  <TableCell
                    sx={{
                      fontWeight: 600,
                      fontSize: 12,
                      color: "text.secondary",
                      textTransform: "none",
                      letterSpacing: 0,
                      py: 1,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      // Headers stay on one line, and the text columns keep
                      // a floor (each includes the cell's 12 px side
                      // padding) so no label or value wraps a word per line.
                      // A pane narrower than the floors scrolls the table
                      // sideways, with the fade above as the cue.
                      whiteSpace: "nowrap",
                      minWidth: 136,
                    }}
                  >
                    Label
                  </TableCell>
                  <TableCell
                    sx={{
                      fontWeight: 600,
                      fontSize: 12,
                      color: "text.secondary",
                      textTransform: "none",
                      letterSpacing: 0,
                      py: 1,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      whiteSpace: "nowrap",
                      minWidth: 150,
                    }}
                  >
                    Value
                  </TableCell>
                  <TableCell
                    sx={{
                      fontWeight: 600,
                      fontSize: 12,
                      color: "text.secondary",
                      textTransform: "none",
                      letterSpacing: 0,
                      py: 1,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      whiteSpace: "nowrap",
                    }}
                  >
                    Annotator
                  </TableCell>
                  <TableCell
                    sx={{
                      fontWeight: 600,
                      fontSize: 12,
                      color: "text.secondary",
                      textTransform: "none",
                      letterSpacing: 0,
                      py: 1,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      whiteSpace: "nowrap",
                      // Room for "11 Nov 2026" on one line, the time on a
                      // second.
                      minWidth: 96,
                    }}
                  >
                    Updated At
                  </TableCell>
                  <TableCell
                    sx={{
                      fontWeight: 600,
                      fontSize: 12,
                      color: "text.secondary",
                      textTransform: "none",
                      letterSpacing: 0,
                      py: 1,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      whiteSpace: "nowrap",
                      minWidth: 110,
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
                        <Typography
                          component="span"
                          sx={{
                            fontSize: 13,
                            color: "text.primary",
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {formatScoreValue(row.labelType, row.value)}
                        </Typography>
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
                                ? "solar:user-linear"
                                : "solar:plug-circle-linear"
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
                      <Typography
                        sx={{
                          fontSize: 12,
                          color: "text.secondary",
                          // Two even lines (date, then time) rather than
                          // the time's "AM" alone on a second line.
                          textWrap: "balance",
                        }}
                      >
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
        </Box>
      )}

      {/* Whole-item notes live on the source span, even when scores live on trace. */}
      {spanNotes.length > 0 && (
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
                        fontSize: 12,
                        color: "text.secondary",
                        textTransform: "none",
                        letterSpacing: 0,
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
                            icon="solar:user-linear"
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
  projectId: PropTypes.string,
  title: PropTypes.string,
  renderActions: PropTypes.node,
  openQueueItemOnRowClick: PropTypes.bool,
  shared: PropTypes.shape({
    scores: PropTypes.array,
    spanNotes: PropTypes.array,
  }),
};
