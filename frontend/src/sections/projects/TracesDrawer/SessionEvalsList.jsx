import ErrorState from "src/components/error-state";
import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography, useTheme } from "@mui/material";
import { useInfiniteQuery } from "@tanstack/react-query";
import { format, parseISO } from "date-fns";
import axios, { endpoints } from "src/utils/axios";
import EvalStatusIndicator from "src/components/eval/EvalStatusIndicator";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { EVAL_STATUS, getEvalNonScoreStatus } from "src/utils/evalStatus";
import ContentSkeleton from "src/components/content-skeleton";
import { actionErrorMessage } from "src/sections/projects/requestErrors";

// The tab checks again on its own while a result can still arrive: while the
// list is empty (a new result takes a few seconds to reach the read store) and
// while a row is still being scored. Each wait stops after a fixed number of
// checks, so a row that never finishes cannot keep the tab polling.
const POLL_MS = 5000;
const EMPTY_POLL_LIMIT = 12;
const IN_FLIGHT_POLL_LIMIT = 120;

const PASS_LABELS = ["Passed", "Pass"];
const FAIL_LABELS = ["Failed", "Fail"];

// Eval output is JSON text such as {"score": 1, "choice": "Pass"}; older rows
// can hold the same dict written with single quotes.
const readOutput = (value) => {
  if (value && typeof value === "object") return value;
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text.startsWith("{") && !text.startsWith("[")) return null;
  try {
    return JSON.parse(text);
  } catch {
    try {
      return JSON.parse(text.replace(/'/g, '"'));
    } catch {
      return null;
    }
  }
};

const choiceText = (value) => {
  if (Array.isArray(value)) {
    const parts = value
      .map((part) =>
        part && typeof part === "object" ? choiceText(part) : String(part),
      )
      .filter(Boolean);
    return parts.length ? parts.join(", ") : null;
  }
  if (!value || typeof value !== "object") return null;
  const choice = value.choice ?? value.choices;
  if (Array.isArray(choice)) return choiceText(choice);
  return typeof choice === "string" && choice ? choice : null;
};

const hasValue = (value) => value != null && value !== "";

// Score, readable result and lifecycle of one row. A direct score wins;
// otherwise the score and choice come from the eval's JSON output, the same
// way the task Logs table reads span evals.
const readSessionEval = (item) => {
  const detail = item.detail || {};
  const resultOutput = readOutput(item.result);
  const output = readOutput(detail.output_str) ?? resultOutput;
  const outputScore =
    output && typeof output.score === "number" ? output.score : null;
  const score = item.score ?? outputScore;
  const label =
    choiceText(output) ?? (resultOutput ? null : item.result || null);
  const errored = item.status === "error" || item.eval_status === "errored";
  const settled =
    errored ||
    score != null ||
    Boolean(label) ||
    Boolean(item.reason) ||
    [
      detail.output_bool,
      detail.output_float,
      detail.output_str,
      detail.error_message,
    ].some(hasValue);
  // The API names queued, running and skipped rows when it knows; a row with
  // no score, result, reason or error has not been scored yet.
  const lifecycle =
    getEvalNonScoreStatus(item.eval_status || item.status) ||
    (settled ? null : EVAL_STATUS.PENDING);
  return { score, label, errored, lifecycle };
};

const isInFlight = (item) => {
  const { lifecycle } = readSessionEval(item);
  return lifecycle === EVAL_STATUS.PENDING || lifecycle === EVAL_STATUS.RUNNING;
};

const ScoreChip = ({ value }) => {
  if (value == null) {
    return (
      <Typography variant="caption" color="text.disabled">
        —
      </Typography>
    );
  }
  if (typeof value === "number") {
    // Same bands as before the design system: 0.7 and up passes, below 0.3 fails.
    return (
      <StatusBadge
        role={value >= 0.7 ? "success" : value >= 0.3 ? "warning" : "danger"}
        label={value.toFixed(2)}
        variant="badge"
        dot={false}
        size="sm"
      />
    );
  }
  return (
    <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
      {String(value)}
    </Typography>
  );
};
ScoreChip.propTypes = { value: PropTypes.any };

const StatusChip = ({ lifecycle, errored, result, skippedReason }) => {
  // Not-yet-completed evals (pending/running/skipped) render a loading /
  // queued / skipped indicator instead of a terminal result chip.
  if (lifecycle) {
    return (
      <EvalStatusIndicator status={lifecycle} skippedReason={skippedReason} />
    );
  }
  if (errored) {
    return <EvalStatusIndicator status="errored" />;
  }
  if (PASS_LABELS.includes(result)) {
    return <StatusBadge status="passed" label={result} />;
  }
  if (FAIL_LABELS.includes(result)) {
    return <StatusBadge status="failed" label={result} />;
  }
  return result ? (
    <Typography variant="body2" color="text.secondary">
      {result}
    </Typography>
  ) : (
    <Typography variant="body2" color="text.disabled">
      —
    </Typography>
  );
};
StatusChip.propTypes = {
  lifecycle: PropTypes.string,
  errored: PropTypes.bool,
  result: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  skippedReason: PropTypes.string,
};

const Row = ({ item }) => {
  const theme = useTheme();
  const { score, label, errored, lifecycle } = readSessionEval(item);
  const created = item.created_at
    ? format(parseISO(item.created_at), "dd/MM/yyyy HH:mm")
    : "—";
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "1.5fr 1fr 1fr 2fr 1fr",
        alignItems: "center",
        gap: 1.5,
        px: 1.5,
        py: 1.25,
        borderBottom: `1px solid ${theme.palette.divider}`,
      }}
    >
      <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
        {item.eval_name || "—"}
      </Typography>
      <ScoreChip value={score} />
      <StatusChip
        lifecycle={lifecycle}
        errored={errored}
        result={label}
        skippedReason={item.skipped_reason || item.reason}
      />
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={item.reason}
      >
        {item.reason || "—"}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {created}
      </Typography>
    </Box>
  );
};
Row.propTypes = { item: PropTypes.object.isRequired };

const SessionEvalsList = ({ sessionId, pollIntervalMs = POLL_MS }) => {
  const theme = useTheme();
  // Data updates already counted when checking began for the current session.
  const pollStart = React.useRef({ queryHash: null, updates: 0 });
  const {
    data,
    isLoading,
    isError,
    error,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
    refetch,
  } = useInfiniteQuery({
    queryKey: ["sessionEvalLogs", sessionId],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      axios
        // page is 0-indexed on the backend (offset = page * page_size); the
        // response shape is { total, page, page_size, items }.
        .get(endpoints.project.getSessionEvalLogs(sessionId), {
          params: { page: pageParam, page_size: 100 },
        })
        .then((res) => res.data?.result || { items: [], total: 0 }),
    getNextPageParam: (lastPage, pages) => {
      const loadedCount = pages.reduce(
        (count, page) => count + page.items.length,
        0,
      );
      return lastPage.items.length > 0 && loadedCount < lastPage.total
        ? lastPage.page + 1
        : undefined;
    },
    enabled: Boolean(sessionId),
    refetchInterval: (query) => {
      const { state } = query;
      if (state.status !== "success") return false;
      if (pollStart.current.queryHash !== query.queryHash) {
        pollStart.current = {
          queryHash: query.queryHash,
          updates: state.dataUpdateCount,
        };
      }
      const checks = state.dataUpdateCount - pollStart.current.updates;
      const loaded =
        state.data?.pages?.flatMap((page) => page?.items || []) || [];
      let limit = 0;
      if (loaded.length === 0) limit = EMPTY_POLL_LIMIT;
      else if (loaded.some(isInFlight)) limit = IN_FLIGHT_POLL_LIMIT;
      return checks < limit ? pollIntervalMs : false;
    },
  });

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <ContentSkeleton variant="rows" />
      </Box>
    );
  }

  // A failed background check keeps the rows already on screen.
  if (isError && !isFetchNextPageError && !data) {
    return (
      <ErrorState
        title="Failed to load session evals"
        detail={actionErrorMessage(error, {
          fallback: "Couldn't load evals for this session.",
        })}
        onRetry={() => refetch()}
        sx={{ height: "auto", flexGrow: 0 }}
      />
    );
  }

  const items = data?.pages.flatMap((page) => page.items) || [];

  if (items.length === 0) {
    return (
      <Stack alignItems="center" spacing={1} sx={{ py: 4, px: 2 }}>
        <Typography variant="body2" color="text.secondary">
          No session-level evaluations yet.
        </Typography>
        <Typography variant="caption" color="text.secondary" textAlign="center">
          Configure a task with row type{" "}
          <Box component="span" sx={{ fontWeight: 600 }}>
            Sessions
          </Box>{" "}
          to evaluate this session.
        </Typography>
      </Stack>
    );
  }

  return (
    <Box>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "1.5fr 1fr 1fr 2fr 1fr",
          gap: 1.5,
          px: 1.5,
          py: 1,
          backgroundColor: theme.palette.background.default,
          borderBottom: `1px solid ${theme.palette.divider}`,
        }}
      >
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          Eval
        </Typography>
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          Score
        </Typography>
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          Result
        </Typography>
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          Reason
        </Typography>
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          When
        </Typography>
      </Box>
      {items.map((item) => (
        <Row key={item.id} item={item} />
      ))}
      {hasNextPage && (
        <Stack alignItems="center" spacing={1} sx={{ py: 2 }}>
          {isFetchNextPageError && (
            <ErrorState
              title="Failed to load more session evals"
              detail={error?.message || "unknown error"}
              sx={{ height: "auto", flexGrow: 0 }}
            />
          )}
          {/* Background checks never lock Load more. */}
          <Button
            variant="outlined"
            disabled={isFetchingNextPage}
            onClick={() => fetchNextPage()}
          >
            {isFetchingNextPage
              ? "Loading more..."
              : isFetchNextPageError
                ? "Retry loading more"
                : "Load more"}
          </Button>
        </Stack>
      )}
    </Box>
  );
};

SessionEvalsList.propTypes = {
  sessionId: PropTypes.string,
  pollIntervalMs: PropTypes.number,
};

export default SessionEvalsList;
