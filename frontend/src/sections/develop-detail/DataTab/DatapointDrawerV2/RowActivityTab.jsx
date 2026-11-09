import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Chip, Skeleton, Stack, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import CustomTooltip from "src/components/tooltip";
import ActivityList from "src/sections/common/RowPanel/ActivityList";
import EmptyState from "src/sections/common/ux/EmptyState";
import ErrorState from "src/sections/common/ux/ErrorState";
import { useViewUrlState } from "src/sections/common/ux/viewState";
import { fmtCount } from "src/sections/common/format/fmt";
import { useRowActivity } from "src/api/develop/row-view";
import CommentThreads from "src/sections/common/Collab/CommentThread";
import CommentComposer from "src/sections/common/Collab/CommentComposer";
import {
  ALL_RUN_THREADS,
  fieldAnchorOptions,
} from "src/sections/common/Collab/commentModel";
import {
  collabError,
  usePostComment,
  useRowComments,
} from "src/api/develop/row-collab";
import { notify } from "src/sections/common/ux/toast";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { sectionLabelSx } from "../Versions/ChangeParts";
import {
  ACTIVITY_FILTERS as HISTORY_FILTERS,
  COLLAB_ACTIVITY_FILTERS,
  COMMENT_STATUS_FILTERS,
  collabActivityCounts as activityCounts,
  commentStatusCounts,
  filterCollabActivity as filterActivity,
  groupActivity,
  threadStatusCounts,
} from "./activityModel";
import { runResultHref } from "./runsModel";

const VIEW_DEFAULTS = { ract: "all", rcs: "open", rcompose: "", ranchor: "" };

// The Row view's column read (same key as RowView, so it is shared).
const useRowColumns = (datasetId) =>
  useQuery({
    queryKey: ["dataset-detail-average", datasetId],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(datasetId), {
        params: { column_config_only: true },
      }),
    select: (d) => d.data?.result?.column_config ?? [],
    enabled: Boolean(datasetId),
  });

/** W4-1: the composer pinned under the other chips (posts a whole-row or field comment). */
function PinnedComposer({ datasetId, rowId, anchorOptions }) {
  const q = useRowComments(datasetId, rowId);
  const post = usePostComment(datasetId, rowId);
  if (q.data?.me?.perms?.view === false) return null;
  return (
    <Box
      sx={{
        position: "sticky",
        bottom: 0,
        pt: 1,
        pb: 0.5,
        mt: 1,
        bgcolor: "background.paper",
        zIndex: 1,
      }}
    >
      <CommentComposer
        limits={q.data?.limits}
        access={q.data?.access}
        anchorOptions={anchorOptions}
        busy={post.isPending}
        onSubmit={async (body, anchor) => {
          try {
            await post.mutateAsync({ body, anchor });
            return true;
          } catch (e) {
            notify(collabError(e, "Couldn't post the comment."), {
              variant: "error",
            });
            return false;
          }
        }}
      />
    </Box>
  );
}

PinnedComposer.propTypes = {
  datasetId: PropTypes.string.isRequired,
  rowId: PropTypes.string.isRequired,
  anchorOptions: PropTypes.array,
};

/**
 * The row's history (PRODUCT-SPEC §3.4 Activity, N8), newest first:
 * versions (links open the row as of that version with diff on), split
 * changes, queues, scores and every run of the row.
 * R3 (RV#22): filter chips `All 17 · Runs 9 · Edits 3 · Annotations 4 ·
 * Splits 1` (URL `ract`), day headers (Today · Yesterday · This week ·
 * Last week · Older), bursts of runs folded into `Ran in 9 runs · 6 passed`,
 * relative times with the absolute time on hover.
 *
 * L6 launch switch: the tab (dataset.rowTab.activity) hosts two claims.
 * The history feed and its chips need dataset.rowExtras.activity; the
 * threads, the Comments chip and the composer need dataset.collab.comments.
 * With comments alone the tab is the row's threads (every comment door
 * opens here), read from the comments endpoint only.
 */
const RowActivityTab = ({ datasetId, rowId, onOpenVersion }) => {
  const historyShown = isSurfaceShown("dataset.rowExtras.activity");
  const commentsShown = isSurfaceShown("dataset.collab.comments");
  const commentsOnly = commentsShown && !historyShown;
  const navigate = useNavigate();
  const q = useRowActivity(
    datasetId,
    rowId,
    historyShown ? {} : { enabled: false },
  );
  // Comments without the history: the Open · Resolved · All counts come
  // from the threads (the same read as the Row view's, so no extra call).
  const threads = useRowComments(datasetId, rowId, {
    runId: ALL_RUN_THREADS,
    ...(commentsOnly ? {} : { enabled: false }),
  });
  const { data: columns } = useRowColumns(datasetId);
  const anchorOptions = useMemo(() => fieldAnchorOptions(columns), [columns]);
  const [view, setView] = useViewUrlState(VIEW_DEFAULTS);
  const filters = commentsShown ? COLLAB_ACTIVITY_FILTERS : HISTORY_FILTERS;
  // Without the comments claim the history carries no thread events.
  const events = useMemo(() => {
    const all = historyShown ? q.data ?? [] : [];
    return commentsShown ? all : all.filter((e) => e?.type !== "comment");
  }, [q.data, historyShown, commentsShown]);
  const counts = useMemo(() => activityCounts(events), [events]);
  const statusCounts = useMemo(
    () =>
      commentsOnly
        ? threadStatusCounts(threads.data?.threads)
        : commentStatusCounts(events),
    [commentsOnly, threads.data, events],
  );
  const commentStatus = COMMENT_STATUS_FILTERS.some((f) => f.key === view.rcs)
    ? view.rcs
    : "open";
  const active = commentsOnly
    ? "comments"
    : filters.some((f) => f.key === view.ract)
      ? view.ract
      : "all";
  const groups = useMemo(
    () => groupActivity(filterActivity(events, active)),
    [events, active],
  );

  if (historyShown && q.isError)
    return (
      <Box sx={{ p: 2 }}>
        <ErrorState
          thing="this row's activity"
          error={q.error}
          onRetry={() => q.refetch()}
          compact
        />
      </Box>
    );
  if (historyShown && q.isLoading)
    return (
      <Stack gap={1.25} sx={{ p: 2 }}>
        <Skeleton variant="rounded" height={24} width={320} />
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} variant="rounded" height={28} />
        ))}
      </Stack>
    );
  const current = filters.find((f) => f.key === active);
  return (
    <Box sx={{ px: 2, pt: 1.25, pb: 1 }}>
      {historyShown && (
        <Stack
          direction="row"
          gap={0.75}
          data-testid="activity-filters"
          role="group"
          aria-label="Show activity"
          sx={{ flexWrap: "wrap", mb: 0.5 }}
        >
          {filters.map((f) => {
            const n = counts[f.key] ?? 0;
            const on = active === f.key;
            // W4-1: Comments stays clickable at 0 (that is where you start one).
            const always = f.key === "all" || f.key === "comments";
            const chip = (
              <Chip
                size="small"
                label={`${f.label} ${fmtCount(n)}`}
                variant={on ? "filled" : "outlined"}
                color={on ? "primary" : "default"}
                disabled={!n && !always}
                aria-pressed={on}
                data-testid={`activity-chip-${f.key}`}
                onClick={() => setView({ ract: on ? "all" : f.key })}
                sx={{
                  height: 24,
                  typography: "s2",
                  fontVariantNumeric: "tabular-nums",
                  ...(!on && { borderColor: "divider" }),
                }}
              />
            );
            return !n && !always ? (
              <CustomTooltip
                key={f.key}
                show
                size="small"
                placement="top"
                arrow
                title={f.none}
              >
                <span>{chip}</span>
              </CustomTooltip>
            ) : (
              <React.Fragment key={f.key}>{chip}</React.Fragment>
            );
          })}
        </Stack>
      )}
      {active === "comments" ? (
        <Box data-testid="activity-comments">
          <Stack
            direction="row"
            gap={0.5}
            role="group"
            aria-label="Show comments"
            sx={{ mt: 0.75, mb: 1 }}
          >
            {COMMENT_STATUS_FILTERS.map((f) => {
              const on = commentStatus === f.key;
              return (
                <Chip
                  key={f.key}
                  size="small"
                  label={`${f.label} ${fmtCount(statusCounts[f.key] ?? 0)}`}
                  variant={on ? "filled" : "outlined"}
                  color={on ? "primary" : "default"}
                  aria-pressed={on}
                  data-testid={`comment-status-${f.key}`}
                  onClick={() => setView({ rcs: f.key })}
                  sx={{
                    height: 22,
                    typography: "s3",
                    fontVariantNumeric: "tabular-nums",
                    ...(!on && { borderColor: "divider" }),
                  }}
                />
              );
            })}
          </Stack>
          <CommentThreads
            datasetId={datasetId}
            rowId={rowId}
            includeRuns
            status={commentStatus}
            anchorOptions={anchorOptions}
            focusComposer={view.rcompose}
            onComposerFocused={() => setView({ rcompose: "" })}
            composerAnchorKey={view.ranchor}
          />
        </Box>
      ) : events.length === 0 ? (
        <EmptyState
          compact
          icon="mdi:history"
          title="No activity on this row yet"
          body="Edits, split changes, annotations and every run of this row show up here."
        />
      ) : groups.length === 0 ? (
        <EmptyState
          compact
          icon="mdi:filter-remove-outline"
          title={current?.none ?? "Nothing here"}
          actions={[
            {
              label: "Show all activity",
              onClick: () => setView({ ract: "all" }),
            },
          ]}
        />
      ) : (
        groups.map((g) => (
          <Box key={g.key} data-testid="activity-day">
            <Typography sx={{ ...sectionLabelSx, mt: 1.25, mb: 0.25 }}>
              {g.title}
            </Typography>
            <ActivityList
              events={g.events}
              onOpenVersion={onOpenVersion}
              onOpenRun={(ev) => navigate(runResultHref(ev, rowId))}
              onOpenComment={
                commentsShown
                  ? (ev) =>
                      setView({
                        ract: "comments",
                        rcs: ev.deleted
                          ? "all"
                          : ev.resolved
                            ? "resolved"
                            : "open",
                      })
                  : undefined
              }
            />
          </Box>
        ))
      )}
      {commentsShown && active !== "comments" && rowId && (
        <PinnedComposer
          datasetId={datasetId}
          rowId={rowId}
          anchorOptions={anchorOptions}
        />
      )}
    </Box>
  );
};

RowActivityTab.propTypes = {
  datasetId: PropTypes.string.isRequired,
  rowId: PropTypes.string.isRequired,
  onOpenVersion: PropTypes.func,
};

export default RowActivityTab;
