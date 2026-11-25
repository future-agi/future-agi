import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Box, Button, Link, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import {
  metricKeys,
  useMetricCalculationStatus,
  useMetricHistory,
} from "./api";
import {
  attemptState,
  awaitsFirstCalculation,
  firstCalculationBadge,
  firstCalculationWord,
  summarizeAttempts,
} from "./metricCalculationState";
import { formatMetricTime, formatRelativeMetricTime } from "./metricTime";
import MetricStatusLine, { MetricCoverageLine } from "./MetricStatusLine";

// How often a metric waiting for its first calculation is read again while
// its Overview is open. Polling pauses while the browser tab is hidden.
const POLL_MS = 30000;

// In the header's words (metricCalculationState), so one state reads one way
// on the page. When most recent attempts stopped, it says what a person can
// do without guessing why they stopped.
function description(draft, attempts) {
  const word = firstCalculationWord(draft);
  if (word === "failed")
    return "The first calculation failed, so this metric has no values yet. Use Run now to queue it again, or edit the definition.";
  const lead = `The first calculation is ${word}. Values, the trend and drivers appear here when it finishes.`;
  // The count of stopped attempts is stated under Latest attempt.
  return attempts.mostStopped
    ? `${lead} If attempts keep stopping, edit the definition or contact support.`
    : `${lead} Nothing is shown as zero in the meantime.`;
}

// "just now" for the first minute, as the page has only just been saved or
// checked; otherwise the relative time.
function ago(value, now, within = 60000) {
  const age = now - new Date(value ?? NaN).getTime();
  return age >= 0 && age < within
    ? "just now"
    : formatRelativeMetricTime(value, { now: new Date(now) });
}

// The last settled read, kept while the same read runs again, so a poll
// never blanks the panel. "Checked …" says how old it is.
function useLastRead(query) {
  const [last, setLast] = useState(null);
  if (query.data && query.data !== last) setLast(query.data);
  return query.data ?? last;
}

function Fact({ label, children }) {
  return (
    <Stack direction="row" spacing={2} sx={{ py: 1 }}>
      <Typography
        component="dt"
        variant="body2"
        color="text.secondary"
        sx={{ width: 140, flexShrink: 0 }}
      >
        {label}
      </Typography>
      <Box component="dd" sx={{ m: 0, minWidth: 0, flex: 1 }}>
        {children}
      </Box>
    </Stack>
  );
}
Fact.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

/** What a person sees between saving a metric and its first values: only
 * what the server has reported (when it was saved, the calculation's state
 * and progress, the latest attempt), and when the page last checked. */
export default function MetricFirstCalculation({
  metric,
  scope,
  onViewHistory,
}) {
  const client = useQueryClient();
  const status = useMetricCalculationStatus({
    scope,
    metricId: metric.id,
    refetchInterval: (query) =>
      awaitsFirstCalculation(query.state.data ?? metric) ? POLL_MS : false,
  });
  const current = useLastRead(status) ?? metric;
  const polling = awaitsFirstCalculation(current);
  const history = useMetricHistory({
    scope,
    metricId: metric.id,
    // The History tab's first page, so both share one read.
    params: { limit: 30 },
    refetchInterval: polling ? POLL_MS : false,
  });
  const historyData = useLastRead(history);
  const activated = Boolean(current.definition);
  const reloaded = useRef(false);
  useEffect(() => {
    // The first calculation finished: read the page's metric again, once,
    // which replaces this panel with its values.
    if (!activated || reloaded.current) return;
    reloaded.current = true;
    void client.invalidateQueries({
      queryKey: metricKeys.workspace(scope),
      predicate: ({ queryKey }) =>
        queryKey[4] === "detail" && queryKey[5] === metric.id,
    });
  }, [activated, client, metric.id, scope]);
  // A poll found the saved version further along (pending to running, or
  // more days done): the page's own read of the metric takes the same
  // response, so the header and this panel never name two states. No read.
  const polled = status.data;
  useEffect(() => {
    const next = polled?.draft_definition;
    const shown = metric.draft_definition;
    if (!next || polled.definition) return;
    if (
      next.id === shown?.id &&
      ["backfill_status", "backfill_days_done", "backfill_days_total"].every(
        (key) => next[key] === shown?.[key],
      )
    )
      return;
    client.setQueriesData(
      {
        queryKey: metricKeys.workspace(scope),
        predicate: ({ queryKey }) =>
          queryKey[4] === "detail" && queryKey[5] === metric.id,
      },
      polled,
    );
  }, [polled, metric.draft_definition, metric.id, client, scope]);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 10000);
    return () => clearInterval(timer);
  }, []);

  const draft = current.draft_definition || metric.draft_definition;
  // The header's badge without its version: not reported reads as pending,
  // as in the header and the list. It never claims "queued".
  const calculation = firstCalculationBadge(draft, {
    short: true,
    version: false,
  });
  const attempts = summarizeAttempts(historyData?.results);
  const latest = attempts.latest && attemptState(attempts.latest.status);
  const checkedAt = status.dataUpdatedAt || null;
  const checking = status.isFetching || history.isFetching;
  // Both are this browser's clock; a check newer than the last tick is
  // just now, not "in 2 seconds".
  const checked = ago(checkedAt, Math.max(now, checkedAt ?? 0), 10000);
  return (
    <Box
      data-testid="metric-not-calculated"
      sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}
    >
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="flex-start"
        sx={{ px: 2.5, pt: 2.5, pb: 2 }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 32,
            height: 32,
            flexShrink: 0,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            color: "text.secondary",
          }}
        >
          <Iconify icon="solar:clock-circle-linear" width={18} />
        </Box>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography component="h2" variant="subtitle1" fontWeight={600}>
            Not calculated yet
          </Typography>
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ maxWidth: 640 }}
          >
            {description(draft, attempts)}
          </Typography>
        </Box>
        <Button
          color="inherit"
          variant="outlined"
          size="small"
          startIcon={<Iconify icon="solar:refresh-linear" width={16} />}
          disabled={checking}
          onClick={() => {
            void status.refetch();
            void history.refetch();
          }}
          sx={{ flexShrink: 0 }}
        >
          {checking ? "Checking…" : "Check now"}
        </Button>
      </Stack>
      <Box
        component="dl"
        aria-label="First calculation"
        sx={{
          m: 0,
          mx: 2.5,
          py: 0.5,
          borderTop: 1,
          borderColor: "divider",
        }}
      >
        {draft?.created_at && (
          <Fact label="Saved">
            <Typography
              variant="body2"
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {formatMetricTime(draft.created_at)}
              <Box component="span" sx={{ color: "text.secondary" }}>
                {" "}
                · {ago(draft.created_at, now)}
              </Box>
            </Typography>
          </Fact>
        )}
        <Fact label="First calculation">
          <Stack spacing={1}>
            <StatusBadge {...calculation} variant="dot" />
            {/* Started with no history: say where values will begin. */}
            <MetricCoverageLine definition={draft} />
            {/* Progress only, when reported; never an estimate. */}
            {Number.isSafeInteger(draft?.backfill_days_total) &&
              draft.backfill_days_total > 0 && (
                <MetricStatusLine
                  definition={{
                    backfill_days_done: draft.backfill_days_done,
                    backfill_days_total: draft.backfill_days_total,
                  }}
                  showFreshness={false}
                />
              )}
          </Stack>
        </Fact>
        <Fact label="Latest attempt">
          {latest ? (
            <Stack spacing={0.5}>
              <Stack
                direction="row"
                spacing={1}
                alignItems="center"
                flexWrap="wrap"
              >
                <StatusBadge {...latest} variant="dot" />
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ fontVariantNumeric: "tabular-nums" }}
                >
                  started {formatMetricTime(attempts.latest.created_at)}
                </Typography>
              </Stack>
              {attempts.stopped > 0 && (
                <Typography variant="body2" color="text.secondary">
                  {attempts.stopped === attempts.total
                    ? `The last ${attempts.total === 1 ? "attempt" : `${attempts.total} attempts`} stopped before finishing.`
                    : `${attempts.stopped} of the last ${attempts.total} attempts stopped before finishing.`}{" "}
                  {onViewHistory && (
                    <Link
                      component="button"
                      type="button"
                      variant="body2"
                      color="text.primary"
                      underline="always"
                      onClick={onViewHistory}
                      sx={{ verticalAlign: "baseline" }}
                    >
                      View history
                    </Link>
                  )}
                </Typography>
              )}
            </Stack>
          ) : (
            <Typography variant="body2" color="text.secondary">
              {historyData
                ? "None recorded yet"
                : history.readState === "loading"
                  ? "Checking…"
                  : "Unavailable"}
            </Typography>
          )}
        </Fact>
      </Box>
      <Typography
        variant="caption"
        color="text.secondary"
        component="p"
        sx={{
          m: 0,
          px: 2.5,
          py: 1.25,
          borderTop: 1,
          borderColor: "divider",
        }}
      >
        {checkedAt ? `Checked ${checked}.` : "Checking…"}
        {status.readState !== "ready" &&
          status.readState !== "loading" &&
          " The last check failed; the facts above may be out of date."}
        {polling && " Checks again every 30 seconds while Overview is open."}
      </Typography>
    </Box>
  );
}
MetricFirstCalculation.propTypes = {
  metric: PropTypes.object.isRequired,
  scope: PropTypes.object.isRequired,
  onViewHistory: PropTypes.func,
};
