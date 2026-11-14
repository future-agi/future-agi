import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  InputAdornment,
  Link,
  Skeleton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import { Link as RouterLink } from "react-router-dom";
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import { unitWord } from "src/sections/projects/observePopulation";
import { isLiveRun, useInstantEvalHistory } from "../useInstantEval";
import { answerText } from "../instantEvalAnswers";
import { formatAgo } from "../instantEvalUnits";
import { answerColor } from "../results/answerColors";
import { dayGroup } from "./askFormat";
import { sourceViewTitle } from "./askLinks";
import { displayQuestions, isNotFound } from "./useAskRun";
import { answeredSplit, savedAnswerCount } from "./askFilter";

const ASK_RAIL_WIDTH = 320;
const SEARCH_DEBOUNCE_MS = 250;

// The first question's answers as a thin bar, with the rows Jev couldn't
// answer hatched in red at its end.
function SplitBar({ item, counts }) {
  const theme = useTheme();
  const { labels, answered, failed } = answeredSplit(item, counts);
  const total = answered + failed;
  if (!total) return null;
  const text = [
    ...labels.map((entry) => `${answerText(entry.label)} ${entry.count}`),
    ...(failed ? [`couldn't answer ${failed}`] : []),
  ].join(" · ");
  return (
    <Box
      role="img"
      aria-label={`Answers: ${text}`}
      title={text}
      sx={{
        display: "flex",
        height: 6,
        borderRadius: 3,
        overflow: "hidden",
        bgcolor: alpha(theme.palette.text.disabled, 0.15),
      }}
    >
      {labels.map((entry) => (
        <Box
          key={entry.label}
          sx={{
            width: `${(entry.count / total) * 100}%`,
            bgcolor: answerColor(item, entry.label, theme),
          }}
        />
      ))}
      {failed > 0 && (
        <Box
          data-testid="rail-failed"
          sx={{
            width: `${(failed / total) * 100}%`,
            backgroundImage: `repeating-linear-gradient(135deg, ${theme.palette.status.danger.solid} 0 2px, transparent 2px 4px)`,
          }}
        />
      )}
    </Box>
  );
}

SplitBar.propTypes = {
  item: PropTypes.object.isRequired,
  counts: PropTypes.object,
};

function topAnswer(item, counts) {
  const top = [...answeredSplit(item, counts).labels].sort(
    (a, b) => b.count - a.count,
  )[0];
  return top?.count
    ? `${top.count.toLocaleString()} ${answerText(top.label)}`
    : "";
}

// The open run's page states that are still being answered.
const PAGE_LIVE = new Set(["running", "preparing", "pausing"]);
// The server calls a run stalled after this long without progress once no
// workflow drains it. History asks no workflow, so a live run quiet for
// longer only says it hasn't moved; its page says where it stands.
const QUIET_AFTER_MS = 90_000;
const NOW_TICK_MS = 15_000;

/**
 * Where a run stands on its card: "live" (being answered), "stalled"
 * (stopped before finishing), "quiet" (live, but nothing has moved for
 * longer than the stall bound) or null. The open run's from its page.
 */
function cardState(listed, progress, now) {
  const page = progress?.status;
  if (page && page !== "loading" && page !== "error")
    return PAGE_LIVE.has(page) ? "live" : page === "stalled" ? "stalled" : null;
  if (listed.status === "stalled") return "stalled";
  if (!isLiveRun(listed)) return null;
  const moved = Date.parse(listed.lastActivityAt || "");
  return Number.isFinite(moved) && now - moved > QUIET_AFTER_MS
    ? "quiet"
    : "live";
}

// Each card state through the one status map: a live run pulses, a stopped
// one is a warning, a quiet one is neutral.
const STATE_MARKS = {
  live: { status: "running", label: "Running", title: "Still being answered" },
  stalled: {
    status: "stalled",
    role: "warning",
    label: "Stopped",
    title: "Stopped before finishing",
  },
  quiet: {
    status: "idle",
    role: "neutral",
    label: "No progress lately",
    title: "No progress lately",
  },
};

// Now, moved on while a run is live: a run that stops moving turns quiet on
// its card even when History's polls bring nothing new.
function useNow(ticking) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return undefined;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), NOW_TICK_MS);
    return () => clearInterval(timer);
  }, [ticking]);
  return now;
}

// One past run: its question, view, size, split and age.
function RunCard({
  runItem: listed,
  selected,
  onOpen,
  onRecreate,
  progress,
  now,
}) {
  // The open run counts from the summary its page reads, so the card and
  // the page never show two counts while answers land.
  const runItem = progress
    ? {
        ...listed,
        answered: progress.settled,
        rows: progress.total || listed.rows,
        counts: progress.counts || listed.counts,
      }
    : listed;
  const item = runItem.items[0];
  // As the run's header shows them: without the run's name in front.
  const [question] = displayQuestions(
    runItem.items.map((entry) => entry.question),
  );
  const state = cardState(listed, progress, now);
  const mark = STATE_MARKS[state];
  const failed = runItem.status === "failed";
  const answersSaved = savedAnswerCount(runItem.counts);
  const failedBeforeAnswering = failed && !answersSaved;
  const editable = runItem.items.filter((entry) => entry.templateId);
  // A user question counts users, each once; it also says how many of
  // their sessions it read.
  const rows = runItem.rows;
  const sessions =
    runItem.unit === "users" && runItem.sessions != null
      ? `${runItem.sessions.toLocaleString()} sessions read`
      : null;
  // With several questions a row counts once each has its answer, as the
  // run's page says it ("6 of 49 sessions done with both questions"), not
  // as the first question's answers on the bar below.
  const asked = runItem.items.length;
  const progressWords =
    rows == null
      ? ""
      : `${Math.min(runItem.answered, rows)} of ${rows} ${
          asked > 2
            ? `done with all ${asked}`
            : asked === 2
              ? "done with both"
              : "answered"
        }`;
  // The state itself is the badge in front; the line says how far it got.
  const size =
    rows == null
      ? ""
      : state
        ? progressWords
        : [`${rows.toLocaleString()} ${unitWord(runItem.unit, rows)}`, sessions]
            .filter(Boolean)
            .join(" · ");
  const meta = [
    sourceViewTitle(runItem.unit),
    size,
    formatAgo(runItem.createdAt),
  ]
    .filter(Boolean)
    .join(" · ");
  const open = () => onOpen(runItem);
  return (
    <Box
      role="link"
      tabIndex={0}
      aria-current={selected ? "page" : undefined}
      aria-label={`Open the run: ${question}`}
      onClick={open}
      onKeyDown={(event) => {
        if (event.key === "Enter" && event.target === event.currentTarget)
          open();
      }}
      sx={{
        p: 1.5,
        borderRadius: "8px",
        border: "1px solid",
        borderColor: selected ? "border.selected" : "divider",
        bgcolor: selected ? "action.selected" : "background.paper",
        cursor: "pointer",
        transition: "border-color 120ms",
        "&:hover": selected ? undefined : { borderColor: "border.hover" },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "border.focus",
          outlineOffset: 2,
        },
      }}
    >
      <Typography
        variant="subtitle2"
        title={runItem.items.map((entry) => entry.question).join("\n")}
        sx={{
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          minWidth: 0,
        }}
      >
        {question}
      </Typography>
      {runItem.items.length > 1 && (
        <Typography variant="caption" color="text.secondary" component="p">
          +{runItem.items.length - 1} more question
          {runItem.items.length > 2 ? "s" : ""}
        </Typography>
      )}
      <Stack
        direction="row"
        spacing={0.75}
        alignItems="center"
        sx={{ mt: 0.25, minWidth: 0 }}
      >
        {mark && (
          <StatusBadge
            status={mark.status}
            role={mark.role}
            label={mark.label}
            variant="dot"
            title={mark.title}
            sx={{ flexShrink: 0 }}
          />
        )}
        <Typography
          variant="caption"
          color="text.secondary"
          noWrap
          component="p"
          title={meta}
          sx={{ minWidth: 0, fontVariantNumeric: "tabular-nums" }}
        >
          {meta}
        </Typography>
      </Stack>
      {failed && (
        <Stack spacing={0.5} sx={{ mt: 1 }}>
          <StatusBadge
            status="failed"
            label={
              failedBeforeAnswering
                ? "Failed before answering"
                : `Failed · ${answersSaved.toLocaleString()} ${answersSaved === 1 ? "answer" : "answers"} saved`
            }
            size="sm"
            sx={{ alignSelf: "flex-start" }}
          />
          <Typography variant="caption" color="text.secondary">
            {failedBeforeAnswering
              ? "No answers were saved. Check the evaluator or recreate the question."
              : "The run stopped before finishing. Its saved answers are still available."}
          </Typography>
          {editable.map((entry, index) => (
            <Button
              key={entry.configId}
              size="small"
              variant="outlined"
              component={RouterLink}
              to={`/dashboard/evaluations/${entry.templateId}`}
              onClick={(event) => event.stopPropagation()}
              sx={{ alignSelf: "flex-start" }}
            >
              Fix evaluator{editable.length > 1 ? ` ${index + 1}` : ""}
            </Button>
          ))}
          {editable.length < runItem.items.length && onRecreate && (
            <Button
              size="small"
              variant="outlined"
              onClick={(event) => {
                event.stopPropagation();
                onRecreate(runItem);
              }}
              sx={{ alignSelf: "flex-start" }}
            >
              Recreate question
            </Button>
          )}
        </Stack>
      )}
      {item && runItem.counts?.[item.configId] && (
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <SplitBar item={item} counts={runItem.counts[item.configId]} />
          </Box>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
          >
            {topAnswer(item, runItem.counts[item.configId])}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}

RunCard.propTypes = {
  runItem: PropTypes.object.isRequired,
  selected: PropTypes.bool,
  onOpen: PropTypes.func.isRequired,
  onRecreate: PropTypes.func,
  // The open run's own counts and state: {settled, total, counts, status}.
  progress: PropTypes.object,
  now: PropTypes.number.isRequired,
};

/**
 * Past Instant eval runs of the project beside the open one, newest first and
 * grouped by day, with search and a collapse toggle.
 */
export default function AskRail({
  projectId,
  configsById,
  selectedId,
  collapsed,
  onToggle,
  onNew,
  onOpen,
  onRecreate,
  openProgress,
}) {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);
  const history = useInstantEvalHistory(projectId, configsById, {
    enabled: !collapsed,
    search: query,
  });
  const now = useNow(history.runs.some(isLiveRun));
  const groups = useMemo(() => {
    const out = [];
    history.runs.forEach((runItem) => {
      const name = dayGroup(runItem.createdAt);
      const last = out[out.length - 1];
      if (last?.name === name) last.runs.push(runItem);
      else out.push({ name, runs: [runItem] });
    });
    return out;
  }, [history.runs]);

  if (collapsed)
    return (
      <Stack
        component="nav"
        aria-label="Past questions"
        alignItems="center"
        spacing={1}
        sx={{
          width: 48,
          flexShrink: 0,
          py: 1.5,
          borderRight: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Tooltip title="Show past questions" placement="right" arrow>
          <IconButton
            size="small"
            aria-label="Show past questions"
            onClick={onToggle}
          >
            <Iconify icon="solar:double-alt-arrow-right-linear" width={18} />
          </IconButton>
        </Tooltip>
        <Tooltip title="New question" placement="right" arrow>
          <IconButton size="small" aria-label="New question" onClick={onNew}>
            <Iconify icon="solar:add-linear" width={18} />
          </IconButton>
        </Tooltip>
      </Stack>
    );

  return (
    <Box
      component="nav"
      aria-label="Past questions"
      sx={{
        width: ASK_RAIL_WIDTH,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        borderRight: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Box sx={{ px: 2, pt: 2, pb: 1.5 }}>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Typography
            variant="m3"
            component="h1"
            fontWeight={600}
            sx={{ flex: 1 }}
          >
            Instant eval
          </Typography>
          {/* Secondary: each view beside the rail has its own primary
              action (the composer's Ask, the run's way forward). */}
          <Button
            size="small"
            variant="outlined"
            startIcon={<Iconify icon="solar:add-linear" width={16} />}
            onClick={onNew}
          >
            New question
          </Button>
          <Tooltip title="Hide past questions" arrow>
            <IconButton
              size="small"
              aria-label="Hide past questions"
              onClick={onToggle}
              sx={{ color: "text.secondary" }}
            >
              <Iconify icon="solar:double-alt-arrow-left-linear" width={18} />
            </IconButton>
          </Tooltip>
        </Stack>
        <TextField
          size="small"
          fullWidth
          placeholder="Search questions"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          inputProps={{ "aria-label": "Search past questions" }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifer-linear"
                  width={18}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
          sx={{ mt: 1.5, bgcolor: "background.paper" }}
        />
      </Box>

      <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto", px: 2, pb: 2 }}>
        {history.isLoading ? (
          <Stack spacing={1}>
            {[0, 1, 2, 3].map((key) => (
              <Skeleton key={key} variant="rounded" height={88} />
            ))}
          </Stack>
        ) : history.isError && isNotFound(history.error) ? (
          // The run's page says why; a retry won't bring the project back.
          <Typography variant="body2" color="text.secondary">
            No past questions to show.
          </Typography>
        ) : history.isError ? (
          <Typography variant="body2" color="status.danger.fg">
            Past questions couldn&apos;t be loaded.{" "}
            <Link
              component="button"
              type="button"
              onClick={() => history.refetch()}
            >
              Retry
            </Link>
          </Typography>
        ) : groups.length ? (
          <Stack spacing={2}>
            {groups.map((group) => (
              <Box key={group.name}>
                <Typography
                  variant="s3"
                  color="text.secondary"
                  component="h2"
                  fontWeight="fontWeightMedium"
                  sx={{ display: "block", mb: 0.75 }}
                >
                  {group.name}
                </Typography>
                <Stack spacing={1}>
                  {group.runs.map((runItem) => (
                    <RunCard
                      key={runItem.taskId}
                      runItem={runItem}
                      selected={runItem.taskId === selectedId}
                      onOpen={onOpen}
                      onRecreate={onRecreate}
                      now={now}
                      progress={
                        runItem.taskId === selectedId &&
                        openProgress?.taskId === selectedId
                          ? openProgress
                          : null
                      }
                    />
                  ))}
                </Stack>
              </Box>
            ))}
            {history.hasMore && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => history.loadMore()}
                disabled={history.isFetchingMore}
                sx={{ alignSelf: "flex-start" }}
              >
                {history.isFetchingMore ? "Loading…" : "Show older questions"}
              </Button>
            )}
          </Stack>
        ) : (
          <EmptyState
            variant="no-results"
            icon={query ? "solar:magnifer-linear" : "solar:history-linear"}
            title={
              query ? `No past question matches “${query}”` : "No questions yet"
            }
            description={
              query
                ? undefined
                : "Questions you ask are kept here with their answers."
            }
            onClearFilters={query ? () => setSearch("") : undefined}
            sx={{ px: 0 }}
          />
        )}
      </Box>
    </Box>
  );
}

AskRail.propTypes = {
  projectId: PropTypes.string.isRequired,
  configsById: PropTypes.object.isRequired,
  selectedId: PropTypes.string,
  collapsed: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
  onNew: PropTypes.func.isRequired,
  onOpen: PropTypes.func.isRequired,
  onRecreate: PropTypes.func,
  // The open run's counts and state from its page: {taskId, settled, total,
  // counts, status}.
  openProgress: PropTypes.object,
};
