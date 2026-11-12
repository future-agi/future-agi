import React, { useContext, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Skeleton, Stack, Typography } from "@mui/material";
import G0EmptyState from "src/sections/common/ux/EmptyState";
import ErrorState from "src/sections/common/ux/ErrorState";
import Iconify from "src/components/iconify";
import { useParams } from "react-router";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import SourceCards from "src/sections/common/RunSources/SourceCards";
import {
  cardUnavailableReason,
  stepCardsFor,
} from "src/sections/common/RunSources/catalog";
import { useRunSourceAvailability } from "src/sections/common/RunSources/useRunSourceAvailability";
import { plural } from "src/sections/common/vocab";
import useOpenSource from "./useOpenSource";
import { notRunHere } from "src/sections/common/Runs/notRunHere";
import { ROW_HEIGHT } from "./leaderboardColumns";
import { OtherExperimentsContext } from "../ExperimentTabClassic/otherExperimentsContext";

// EL#42 / A2-11: sized like the content (answer line, the charts only when
// their section is open, 8 rows of 48 px); the toolbar above is real.
const LoadingState = ({ charts }) => (
  <Stack spacing={1.5} sx={{ px: 1.5, pb: 1.5 }} aria-busy="true">
    <Stack direction="row" alignItems="center" gap={1} sx={{ height: 28 }}>
      <Skeleton variant="circular" width={18} height={18} />
      <Skeleton width="48%" />
    </Stack>
    {charts && (
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "1fr 1fr",
            lg: "repeat(4, minmax(0, 1fr))",
          },
          gap: 1.5,
        }}
      >
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} variant="rounded" height={180} />
        ))}
      </Box>
    )}
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      <Skeleton variant="rectangular" height={40} />
      {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
        <Stack
          key={i}
          direction="row"
          alignItems="center"
          gap={2}
          sx={{
            height: ROW_HEIGHT,
            px: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Skeleton variant="rounded" width={18} height={18} />
          <Skeleton width="22%" />
          <Skeleton width="8%" />
          <Skeleton width="12%" />
          <Skeleton width="14%" />
          <Skeleton width="10%" />
          <Skeleton width="10%" />
        </Stack>
      ))}
    </Box>
  </Stack>
);

LoadingState.propTypes = { charts: PropTypes.bool };

// SVP-P1-6c: a faded 3-step table shows what the board will become.
// R4 (lane Rb MUST 8, persona dev-2 / ir-2 / pt-4): an empty Experiments
// tab teaches by offering the six ways to run, inline (Ra's SourceCards,
// same keys and search), above the fold at 1280×720, then the one line
// for people who already have an agent in code.
// "No experiments yet" is said only when the dataset has no experiment at
// all: `otherCount` is how many experiments it has that open in the original
// results view (they are listed under this view).
const EmptyState = ({ otherCount }) => {
  const { dataset: datasetId } = useParams();
  const { kind, dataset } = useDatasetKind(datasetId);
  const openSource = useOpenSource();
  // The cards offer only what the server can run here (as in the drawer).
  const availability = useRunSourceAvailability();
  const [query, setQuery] = useState("");
  const searchRef = useRef(null);
  const rows = Number(dataset?.row_count ?? dataset?.rowCount);
  const noun = kind === "task_suite" ? "task" : "row";
  const title =
    Number.isFinite(rows) && rows > 0
      ? `Run something on these ${plural(rows, noun)}`
      : `Run something on this dataset`;
  // options-truth L1 B (entry:empty-state-doors): keys 1..N (N = the cards
  // shown, three today) pick a card whenever this empty state is on screen
  // and focus is not in a field or a dialog, in the order the cards render
  // (stepCardsFor); a card the server refuses takes no key.
  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || !/^[1-9]$/.test(e.key)) return;
      const t = e.target;
      if (t?.isContentEditable || t?.closest?.("input, textarea, select"))
        return;
      if (document.querySelector('[role="dialog"], .MuiModal-root')) return;
      const card = stepCardsFor(kind, null, availability).cards[
        Number(e.key) - 1
      ];
      // only the cards shown, and that the server runs, have a key
      if (!card || cardUnavailableReason(card, availability)) return;
      e.preventDefault();
      openSource({ cardId: card.id });
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [kind, openSource, availability]);
  return (
    <Stack
      data-testid="experiments-empty"
      sx={{ flex: 1, px: 2, py: 1.5, gap: 1.25, overflowY: "auto" }}
    >
      <Stack direction="row" alignItems="center" gap={1}>
        <Iconify
          icon="mdi:flask-outline"
          width={20}
          sx={{ color: "text.secondary" }}
        />
        <Typography
          typography="m3"
          fontWeight="fontWeightSemiBold"
          data-testid="experiments-empty-title"
        >
          {title}
        </Typography>
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="experiments-empty-line"
        >
          {otherCount === 0
            ? "No experiments yet. Pick one way to run; add another later to see which wins."
            : "Pick one way to run; add another later to see which wins."}
        </Typography>
      </Stack>
      <Box sx={{ maxWidth: 1040, width: "100%" }}>
        <SourceCards
          datasetKind={kind}
          wide
          query={query}
          onQuery={setQuery}
          searchRef={searchRef}
          onPickCard={(cardId) => openSource({ cardId })}
          onPickEntry={(entryId) => openSource({ entryId })}
          availability={availability}
        />
      </Box>
    </Stack>
  );
};

EmptyState.propTypes = { otherCount: PropTypes.number };

// EL#40 / F8: what hides the runs, as removable chips, and the way back.
const FilteredEmpty = ({ onClear, view }) => {
  const openSource = useOpenSource();
  // L7c g2 (entry:leaderboard-not-run-here): rows on a rows dataset.
  const { kind } = useDatasetKind(view?.datasetId);
  const unitNoun = kind === "task_suite" ? "tasks" : "rows";
  // A search names every source; one the server does not run here (or while
  // the report loads or failed) keeps its add action, disabled, with the
  // reason. Never hide it.
  const availability = useRunSourceAvailability({ enabled: Boolean(view?.q) });
  const miss =
    view?.q && !view?.activeFilters?.length
      ? notRunHere(view.q, { datasetId: view.datasetId, availability })
      : null;
  const chips = [];
  if (view?.q)
    chips.push({ label: `Search: ${view.q}`, onDelete: () => view.setQ("") });
  if (view?.activeFilters?.length)
    chips.push({
      label: `${view.activeFilters.length} ${view.activeFilters.length === 1 ? "filter" : "filters"}`,
      onDelete: () => view.setFilters([]),
    });
  if (view?.version && view.version !== "all")
    chips.push({
      label: "Dataset version filter",
      onDelete: () => view.setVersion("all"),
    });
  return (
    <Box
      sx={{
        flex: 1,
        mx: 1.5,
        mb: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        minHeight: 220,
        display: "flex",
      }}
    >
      {/* IT-R2 G2 P1 (rule 14): a search that names a framework or
          harness nobody ran here says so and offers to add it. */}
      {miss ? (
        <G0EmptyState
          icon="mdi:plus-circle-outline"
          title={miss.text}
          body={
            miss.unavailableReason ??
            `${miss.action} to compare it with the ${plural(view?.allRuns?.length ?? 0, "run")} here on the same ${unitNoun}.`
          }
          filterChips={chips}
          onClearFilters={onClear}
          actions={[
            {
              label: miss.action,
              // A source this workspace cannot run says why (body) and its
              // add action stays visible, disabled.
              disabled: Boolean(miss.unavailableReason),
              onClick: miss.unavailableReason
                ? undefined
                : () => openSource({ entryId: miss.entry.id }),
            },
            { label: "Clear search", onClick: onClear },
          ]}
          sx={{ flex: 1 }}
        />
      ) : (
        <G0EmptyState
          icon="mdi:filter-off-outline"
          title={
            view?.q
              ? `No runs match "${view.q}"`
              : "No runs match these filters"
          }
          body={`${view?.allRuns?.length ?? 0} runs are hidden. Try a model, a run label like 2D or 2A·2, or clear the search.`}
          filterChips={chips}
          onClearFilters={onClear}
          actions={[{ label: "Clear filters", onClick: onClear }]}
          sx={{ flex: 1 }}
        />
      )}
    </Box>
  );
};

FilteredEmpty.propTypes = {
  onClear: PropTypes.func.isRequired,
  view: PropTypes.object,
};

const shellBody = (
  { view, strip, grid, footer, answer, chartsOpen, onClearFilters },
  other,
) => {
  const { query } = view;
  if (query.isError && !query.data)
    return (
      <Box sx={{ p: 1.5 }}>
        <ErrorState
          thing="runs"
          error={query.error}
          onRetry={() => query.refetch()}
        />
      </Box>
    );
  if (query.isLoading || !query.data)
    return <LoadingState charts={Boolean(chartsOpen)} />;
  const versionFiltered = view.version !== "all";
  if (!view.allRuns.length && !versionFiltered) {
    // With no runs, whether the dataset has any experiment at all is known
    // only once its other experiments have been read.
    if (other.pending) return <LoadingState charts={Boolean(chartsOpen)} />;
    return <EmptyState otherCount={other.count} />;
  }
  return (
    <Box
      sx={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
    >
      {query.isError && (
        <Alert
          severity="warning"
          sx={{ mx: 1.5, mb: 1 }}
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => query.refetch()}
            >
              Retry
            </Button>
          }
        >
          Couldn&apos;t refresh. Showing the last loaded results.
        </Alert>
      )}
      {view.runs.length ? (
        <>
          {answer}
          {strip}
          <Box
            sx={{
              flex: 1,
              minHeight: 280,
              display: "flex",
              flexDirection: "column",
              px: 1.5,
              pb: 0.5,
            }}
          >
            <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>{grid}</Box>
            {footer}
          </Box>
        </>
      ) : (
        <FilteredEmpty onClear={onClearFilters} view={view} />
      )}
    </Box>
  );
};

/**
 * Loading (8 skeleton rows, the 4 cards when Charts is open), error (Alert
 * + Retry, never the empty state), empty, filtered empty, and the loaded
 * answer + Charts section + grid + status footer.
 *
 * When the tab lists the experiments that open in the original results view
 * (OtherExperimentsContext), their group sits under the runs, in one scrolling
 * column; without it the runs view renders exactly as before.
 */
export const LeaderboardShell = (props) => {
  const other = useContext(OtherExperimentsContext);
  const body = shellBody(props, other);
  if (!other.group) return body;
  return (
    <Box
      data-testid="experiments-tab-groups"
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        overflowY: "auto",
      }}
    >
      <Box sx={{ flex: "1 0 auto", display: "flex", flexDirection: "column" }}>
        {body}
      </Box>
      {other.group}
    </Box>
  );
};

LeaderboardShell.propTypes = {
  view: PropTypes.object.isRequired,
  strip: PropTypes.node,
  grid: PropTypes.node,
  footer: PropTypes.node,
  answer: PropTypes.node,
  chartsOpen: PropTypes.bool,
  onClearFilters: PropTypes.func.isRequired,
};
