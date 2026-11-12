import React from "react";
import PropTypes from "prop-types";
import { Link, Stack, Typography } from "@mui/material";
import { useLocation, useNavigate } from "react-router";
import CustomTooltip from "src/components/tooltip";
import { fmtCount } from "src/sections/common/format/fmt";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import useQuickTests, {
  QUICK_TESTS_PARAM,
} from "src/sections/experiment-detail/iterate/useQuickTests";
import LivePill, { useMenuOpen } from "./LivePill";
import NotRecordedLink from "src/sections/common/RecordRuns/NotRecordedLink";

/**
 * `9 runs + baseline · 2 experiments` (EL#48: the pinned copy is not a
 * second run), or `3 of 10 runs · 1 experiment` while filtered (EL#40).
 */
export function countText(view) {
  const exps = new Set(view.runs.map((r) => r.experiment_id)).size;
  const runs = view.runs.length;
  const expText = fmtCount(exps, "experiment");
  if (view.isFiltered)
    return `${runs} of ${fmtCount(view.allRuns.length, "run")} · ${expText}`;
  const baseId = view.baselineRun?.run_id;
  if (baseId && view.runs.some((r) => r.run_id === baseId))
    return `${fmtCount(runs - 1, "run")} + baseline · ${expText}`;
  return `${fmtCount(runs, "run")} · ${expText}`;
}

const isMoving = (r) => {
  const s = getRunStatus(r);
  return s.active || s.waiting;
};

/** `1 quick test · Show` (R2-SPEC §4.3.4): quick tests stay off the board. */
const QuickTestsLink = ({ datasetId }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { data: quick } = useQuickTests(datasetId);
  const n = quick?.length ?? 0;
  if (!n) return null;
  const params = new URLSearchParams(location.search);
  const shown = params.get(QUICK_TESTS_PARAM) === "1";
  if (shown) params.delete(QUICK_TESTS_PARAM);
  else params.set(QUICK_TESTS_PARAM, "1");
  return (
    <Typography typography="s3" color="text.secondary" noWrap>
      {`${n} quick ${n === 1 ? "test" : "tests"} · `}
      <CustomTooltip
        show
        title="Quick tests from the Iterate dock are not listed on the leaderboard"
        size="small"
        arrow
      >
        <Link
          component="button"
          type="button"
          typography="s3"
          underline="hover"
          data-testid="quick-tests-toggle"
          onClick={() =>
            navigate(
              { pathname: location.pathname, search: `?${params.toString()}` },
              { replace: true },
            )
          }
        >
          {shown ? "Hide" : "Show"}
        </Link>
      </CustomTooltip>
    </Typography>
  );
};

QuickTestsLink.propTypes = { datasetId: PropTypes.string };

/**
 * A2-03 (P7: counts live in footers): the grid's status line,
 * `9 runs + baseline · 2 experiments · updated 1s ago`; live runs show a
 * pulsing dot and pause while a menu or a selection is open. Quick tests
 * sit on the right.
 */
export default function LeaderboardFooter({ view, selectedCount = 0 }) {
  const menuOpen = useMenuOpen();
  const moving = view.allRuns.some(isMoving);
  return (
    <Stack
      data-toast-clear
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      gap={1.5}
      data-testid="leaderboard-footer"
      sx={{ minHeight: 26, pt: 0.5, minWidth: 0 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        <Typography typography="s3" color="text.secondary" noWrap>
          {countText(view)}
        </Typography>
        {view.query.dataUpdatedAt ? (
          <Typography typography="s3" color="text.disabled" aria-hidden>
            ·
          </Typography>
        ) : null}
        <LivePill
          variant="inline"
          updatedAt={view.query.dataUpdatedAt || null}
          polling={moving}
          paused={menuOpen || selectedCount > 0}
          onResume={() => view.query.refetch()}
        />
      </Stack>
      <Stack direction="row" alignItems="center" gap={1.5}>
        <NotRecordedLink datasetId={view.datasetId} />
        <QuickTestsLink datasetId={view.datasetId} />
      </Stack>
    </Stack>
  );
}

LeaderboardFooter.propTypes = {
  view: PropTypes.object.isRequired,
  selectedCount: PropTypes.number,
};
