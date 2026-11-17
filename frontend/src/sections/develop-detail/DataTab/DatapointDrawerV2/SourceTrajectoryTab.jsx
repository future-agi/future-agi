import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import {
  isRefusedSourceRead,
  useSourceSessionTraces,
} from "src/api/develop/row-source";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import SpanTreeTimeline from "src/components/traceDetail/SpanTreeTimeline";
import SpanDetailPane from "src/components/traceDetail/SpanDetailPane";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import useValueViewMode from "src/sections/common/ValueViewer/useValueViewMode";
import {
  formatDuration,
  isExpiredSource,
  parseConversation,
  sourceKindOf,
} from "src/sections/develop-detail/TaskSuite/itemModel";
import ItemTabStrip from "./ItemTabStrip";
import { expiredCopyText, expiredOriginalText } from "./RowViewStates";
import { useRowCells } from "./itemHooks";
import SavedRowCopy from "./SavedRowCopy";

const spanOf = (entry) => entry?.observation_span || {};

function findEntry(entries, id) {
  for (const entry of entries ?? []) {
    if (spanOf(entry).id === id) return entry;
    const hit = findEntry(entry.children, id);
    if (hit) return hit;
  }
  return null;
}

const Note = ({ children, tone = "neutral" }) => (
  <Stack
    direction="row"
    alignItems="center"
    gap={0.75}
    data-testid="trajectory-note"
    sx={{
      px: 1.25,
      py: 0.75,
      borderRadius: 1,
      typography: "s2",
      bgcolor: (t) =>
        tone === "amber"
          ? alpha(t.palette.amber[500], 0.12)
          : t.palette.background.neutral,
    }}
  >
    <Iconify
      icon={tone === "amber" ? "mdi:history" : "mdi:information-outline"}
      width={16}
      sx={{
        color: tone === "amber" ? "amber.700" : "text.secondary",
        flexShrink: 0,
      }}
    />
    <Typography typography="s2">{children}</Typography>
  </Stack>
);
Note.propTypes = { children: PropTypes.node, tone: PropTypes.string };

/**
 * Trajectory tab of the Row view (R2-SPEC §7.3.6): the trace the row was
 * imported from, in the existing SpanTreeTimeline + SpanDetailPane, read in
 * the source's own project; a session source lists its turns' traces.
 * `Open original ↗` opens the same trace in the trace drawer. When the
 * original expired, or the trace read refuses it, the tab shows the copy
 * saved at import and never reads the trace.
 *
 * Props (frozen):
 * @param {string} props.datasetId
 * @param {string} props.rowId
 * @param {object} props.row  get-row-data row (`row_meta.source`, `kind`)
 */
const SourceTrajectoryTab = ({ datasetId, row }) => {
  const source = row?.row_meta?.source ?? null;
  const kind = sourceKindOf(source);
  const expired = isExpiredSource(source);
  const [mode, setMode] = useValueViewMode();
  const sessionQ = useSourceSessionTraces(source?.session_id, {
    enabled: kind === "session" && !source?.trace_id && !expired,
    projectId: source?.project_id,
  });
  const turns = sessionQ.data ?? [];
  // Trace i of a session = the i-th agent turn of the saved conversation.
  const cells = useRowCells(datasetId, row);
  const convValue = cells.byType.conversation?.value;
  const agentTurnNs = useMemo(
    () =>
      (parseConversation(convValue)?.turns ?? [])
        .filter((t) => t.role === "agent")
        .map((t) => t.n),
    [convValue],
  );
  const [picked, setPicked] = useState(null);
  useEffect(() => setPicked(null), [row?.row_id]);
  const traceId = source?.trace_id ?? picked ?? turns[0]?.trace_id ?? null;
  const { data, isLoading, isError, error } = useGetTraceDetail(
    expired ? null : traceId,
    { projectId: source?.project_id },
  );
  // A failed extra page does not invalidate the session pages already loaded.
  const sessionFailed = sessionQ.isError && !sessionQ.isFetchNextPageError;
  // A refused read (a trace deleted since, or not in this caller's
  // projects) reads as expired: the saved copy, never the trace.
  const gone =
    expired ||
    (isError && isRefusedSourceRead(error)) ||
    (sessionFailed && isRefusedSourceRead(sessionQ.error));
  const failed = !gone && (isError || sessionFailed);
  const spans = gone ? undefined : data?.observation_spans;
  const rootId = useMemo(
    () =>
      spans?.find((e) => !spanOf(e).parent_span_id)?.observation_span?.id ??
      null,
    [spans],
  );
  const [selected, setSelected] = useState(null);
  const [fullOpen, setFullOpen] = useState(false);
  useEffect(() => setSelected(rootId), [rootId, traceId]);
  const entry = useMemo(() => findEntry(spans, selected), [spans, selected]);
  const summary = data?.summary;

  return (
    <Box sx={{ minWidth: 0 }}>
      <ItemTabStrip
        mode={mode}
        setMode={setMode}
        copyValue={gone ? null : data ?? null}
        copyLabel="Copy the trace as JSON"
      >
        {summary && !gone && (
          <Typography typography="s3" color="text.secondary" noWrap>
            {`${summary.total_spans ?? spans?.length ?? 0} spans · ${formatDuration(summary.total_latency_ms)}${
              summary.error_count ? ` · ${summary.error_count} errors` : ""
            }`}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        <CustomTooltip
          show={gone || !traceId}
          size="small"
          placement="bottom"
          arrow
          title={gone ? expiredOriginalText(source) : "No trace to open"}
        >
          <span>
            <Button
              size="small"
              variant="outlined"
              disabled={gone || !traceId}
              endIcon={<Iconify icon="mdi:arrow-top-right" width={14} />}
              onClick={() => setFullOpen(true)}
              sx={{ typography: "s2", whiteSpace: "nowrap" }}
              data-testid="open-original"
            >
              Open original
            </Button>
          </span>
        </CustomTooltip>
      </ItemTabStrip>
      <Stack gap={1.25} sx={{ px: 2, pt: 1.5, pb: 3 }}>
        {gone && <Note tone="amber">{expiredCopyText(source)}</Note>}
        {failed && (
          <Note>
            The source trace could not be loaded right now; showing the copy
            saved at import.
          </Note>
        )}
        {(gone || failed) && <SavedRowCopy datasetId={datasetId} row={row} />}
        {!gone &&
          kind === "session" &&
          !source?.trace_id &&
          turns.length > 1 && (
            <Stack
              direction="row"
              flexWrap="wrap"
              gap={0.5}
              role="tablist"
              aria-label="Traces of the session"
            >
              {turns.map((t, i) => {
                const on = t.trace_id === traceId;
                const err = Object.values(t.eval_metrics ?? {}).some(
                  (m) => m?.score === 0,
                );
                return (
                  <CustomTooltip
                    key={t.trace_id}
                    show
                    size="small"
                    placement="top"
                    arrow
                    title={String(t.input ?? "").slice(0, 140) || t.trace_id}
                  >
                    <ButtonBase
                      role="tab"
                      aria-selected={on}
                      onClick={() => setPicked(t.trace_id)}
                      data-testid={`session-trace-${i + 1}`}
                      sx={{
                        height: 24,
                        px: 0.875,
                        borderRadius: 0.75,
                        typography: "s3",
                        border: "1px solid",
                        borderColor: on ? "primary.main" : "divider",
                        bgcolor: on ? "action.selected" : "background.paper",
                        color: on ? "primary.main" : "text.secondary",
                        fontWeight: on ? 600 : 400,
                      }}
                    >
                      {agentTurnNs[i]
                        ? `Turn ${agentTurnNs[i]}`
                        : `Trace ${i + 1}`}
                      {err && (
                        <Box
                          component="span"
                          sx={{ ml: 0.5, color: "red.600", fontWeight: 700 }}
                        >
                          ✗
                        </Box>
                      )}
                    </ButtonBase>
                  </CustomTooltip>
                );
              })}
            </Stack>
          )}
        {!gone &&
          kind === "session" &&
          !source?.trace_id &&
          (sessionQ.hasNextPage || sessionQ.isFetchNextPageError) && (
            <Stack gap={0.75} alignItems="flex-start">
              {sessionQ.isFetchNextPageError && (
                <Typography role="alert" typography="s2" color="text.secondary">
                  More traces could not be loaded. The traces already loaded are kept.
                </Typography>
              )}
              <Button
                size="small"
                variant="outlined"
                onClick={() => sessionQ.fetchNextPage()}
                disabled={sessionQ.isFetchingNextPage}
              >
                {sessionQ.isFetchingNextPage
                  ? "Loading traces…"
                  : sessionQ.isFetchNextPageError
                    ? "Retry loading traces"
                    : "Load more traces"}
              </Button>
            </Stack>
          )}
        {gone || failed ? null : mode === "raw" && data ? (
          <ValueViewer value={data} dataType="json" mode="raw" />
        ) : (isLoading || sessionQ.isLoading) && traceId !== null ? (
          <Stack alignItems="center" sx={{ py: 4 }}>
            <CircularProgress size={20} />
          </Stack>
        ) : !traceId || isError || !spans?.length ? (
          <Typography typography="s2" color="text.secondary" sx={{ py: 2 }}>
            {sessionQ.isLoading
              ? "Loading the session…"
              : "The source trace could not be loaded."}
          </Typography>
        ) : (
          <Stack
            data-testid="source-trace-tree"
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              overflow: "hidden",
            }}
          >
            <Box sx={{ maxHeight: 320, overflow: "auto" }}>
              <SpanTreeTimeline
                spans={spans}
                selectedSpanId={selected}
                onSelectSpan={(id) =>
                  setSelected((prev) => (prev === id ? rootId : id))
                }
              />
            </Box>
            {entry && (
              <Box
                sx={{
                  borderTop: "1px solid",
                  borderColor: "divider",
                  minHeight: 280,
                  display: "flex",
                  flexDirection: "column",
                }}
              >
                <SpanDetailPane
                  entry={entry}
                  allSpans={spans}
                  traceStartTime={spanOf(spans[0]).start_time}
                  isRootSpan={selected === rootId}
                  traceTags={data?.trace?.tags || []}
                  onClose={() => setSelected(rootId)}
                  onSelectSpan={setSelected}
                  drawerOpen
                />
              </Box>
            )}
          </Stack>
        )}
      </Stack>
      {fullOpen && traceId && !gone && (
        <TraceDetailDrawerV2
          traceId={traceId}
          projectId={source?.project_id}
          initialSpanId={source?.span_id}
          open
          onClose={() => setFullOpen(false)}
        />
      )}
    </Box>
  );
};

SourceTrajectoryTab.propTypes = {
  datasetId: PropTypes.string,
  rowId: PropTypes.string,
  row: PropTypes.object,
};

export default SourceTrajectoryTab;
