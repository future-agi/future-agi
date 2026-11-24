import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Tab, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import CandidatesTable from "./CandidatesTable";
import EpochEnding from "./EpochEnding";
import FinishedDiagnosis from "./FinishedDiagnosis";
import FixVerdict from "./FixVerdict";
import FixDecisionDrawer from "./FixDecisionDrawer";
import LaunchFixDialog from "./LaunchFixDialog";
import RetryButton from "./RetryButton";
import ShipButton from "./ShipButton";
import TrialChart from "./TrialChart";
import {
  endingBroke,
  endingLabel,
  endingSentence,
  endingSpent,
  headline,
  noSafeNumbers,
  winnerOf,
} from "./fixRunView";

// The header actions of a finished fix: how it was decided, the winner's
// changes, and Ship. A failed fix's Retry is its ending's one way on.
export function FinishedActions({ fix, onReview }) {
  const [deciding, setDeciding] = useState(false);
  const winner = winnerOf(fix);
  const ready = fix.state === "ready" && !!winner;
  return (
    <>
      {(fix.candidates || []).length > 0 && (
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="solar:shield-check-linear" width={16} />}
          onClick={() => setDeciding(true)}
        >
          How it was decided
        </Button>
      )}
      {ready && (
        <Button
          size="small"
          variant="outlined"
          endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
          onClick={() => onReview(winner.id)}
        >
          {`Review Candidate ${winner.attempt}`}
        </Button>
      )}
      <ShipButton fix={fix} />
      <FixDecisionDrawer
        fix={fix}
        open={deciding}
        onClose={() => setDeciding(false)}
      />
    </>
  );
}
FinishedActions.propTypes = {
  fix: PropTypes.object.isRequired,
  onReview: PropTypes.func.isRequired,
};

// The run page's tab strip (RunDetail), so a fix's tabs read the same.
const TABS_SX = {
  borderBottom: "1px solid",
  borderColor: "divider",
  mb: 2,
};

const TABS = [
  { id: "trials", label: "Candidates" },
  { id: "changes", label: "Changes" },
  { id: "scenarios", label: "Scenarios" },
  { id: "cost", label: "Cost" },
];

// What the no-safe headline rests on, behind a toggle: each trial's rejecting check in
// plain words.
function NoSafeNumbers({ fix }) {
  const [open, setOpen] = useState(false);
  const { trials } = noSafeNumbers(fix);
  if (!trials.length) return null;
  const line = { typography: "s3", color: "text.secondary" };
  return (
    <Box sx={{ mb: 1 }}>
      <Button size="small" onClick={() => setOpen((o) => !o)} sx={{ px: 0 }}>
        {open ? "Hide each candidate's result" : "Show each candidate's result"}
      </Button>
      {open && (
        <Stack spacing={0.5} sx={{ mt: 0.5 }} data-testid="no-safe-numbers">
          {trials.map((t) => (
            <Typography key={t.attempt} sx={line}>
              {`Candidate ${t.attempt} · ${t.label}: ${t.detail}`}
            </Typography>
          ))}
        </Stack>
      )}
    </Box>
  );
}
NoSafeNumbers.propTypes = { fix: PropTypes.object.isRequired };

// What is left to do when a fix found no safe improvement: keep the current
// version, read the evidence, or start a new fix on other cases or parts of the
// agent. Each is an action the product already has.
function WhatToTryNext({ fix, onTab, onLaunch }) {
  const [deciding, setDeciding] = useState(false);
  const tried = (fix.candidates || []).length > 0;
  const line = { typography: "s2", color: "text.secondary" };
  return (
    <Box data-testid="what-to-try-next">
      <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
        What to try next
      </Typography>
      <Stack spacing={0.5}>
        <Typography sx={line}>
          Keep the current version. This epoch changed nothing: your agent
          changes only when you ship a candidate.
        </Typography>
        {tried && (
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={line}>
              Read why each candidate was rejected.
            </Typography>
            <Button size="small" onClick={() => onTab?.("trials")}>
              See the candidates
            </Button>
            <Button size="small" onClick={() => setDeciding(true)}>
              How it was decided
            </Button>
          </Stack>
        )}
        {fix.source_run_id && (
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={line}>
              Pick other failing cases, or let an epoch change other parts of
              the agent.
            </Typography>
            <Button size="small" onClick={onLaunch}>
              Run a new epoch
            </Button>
          </Stack>
        )}
      </Stack>
      {tried && (
        <FixDecisionDrawer
          fix={fix}
          open={deciding}
          onClose={() => setDeciding(false)}
        />
      )}
    </Box>
  );
}
WhatToTryNext.propTypes = {
  fix: PropTypes.object.isRequired,
  onTab: PropTypes.func,
  onLaunch: PropTypes.func.isRequired,
};

// An ending's one way on: retry a failed epoch the platform lets you retry,
// else run a new one where Run Epoch lives, on the issue it came from or the
// run it started from. With neither left, the sentence says what to do.
function endingPrimary(fix, onLaunch) {
  // Retry lives here, never in the header: the ending's one primary.
  if (fix.state === "failed" && fix.retryable) return <RetryButton fix={fix} />;
  const again = {
    size: "small",
    variant: "contained",
    color: "primary",
    startIcon: <Iconify icon="solar:play-linear" width={16} />,
  };
  if (fix.issue) {
    return (
      <Button
        {...again}
        component={RouterLink}
        href={paths.dashboard.errorFeed.detail(fix.issue.cluster_id)}
      >
        Run Epoch again
      </Button>
    );
  }
  if (!fix.source_run_id) return null;
  return (
    <Button {...again} onClick={onLaunch}>
      Run Epoch again
    </Button>
  );
}

// Screen 3: a finished fix. A ready fix leads with its verdict; any other
// ending reads in the one ending layout, says why it ended, and ships nothing.
export default function FixFinishedView({ fix, tab, onTab, renderTab }) {
  const [launching, setLaunching] = useState(false);
  const ready = fix.state === "ready" && !!winnerOf(fix);
  const trials = fix.candidates || [];
  const noSafe = fix.state === "no_improvement";
  const ending = !ready && headline(fix);
  const tabs = ready
    ? TABS
    : TABS.filter((t) => t.id === "trials" || t.id === "cost");
  const current = tabs.find((t) => t.id === tab) ? tab : "trials";
  // The server's sentences, as the pull request says them.
  const flaky = fix.flaky_protected_text || [];
  const launch = () => setLaunching(true);
  return (
    <Box data-testid="fix-finished-view" sx={{ px: 3, py: 2.5 }}>
      {ready && <FixVerdict fix={fix} />}
      {ending && (
        <EpochEnding
          label={endingLabel(fix)}
          danger={endingBroke(fix)}
          sentence={endingSentence(fix)}
          spent={endingSpent(fix)}
          primary={noSafe ? null : endingPrimary(fix, launch)}
        >
          {noSafe && (
            <>
              <NoSafeNumbers fix={fix} />
              <WhatToTryNext fix={fix} onTab={onTab} onLaunch={launch} />
            </>
          )}
        </EpochEnding>
      )}
      <FinishedDiagnosis fix={fix} />
      {flaky.length > 0 && (
        <Box data-testid="flaky-protected" sx={{ mb: 2 }}>
          {flaky.map((line) => (
            <Typography
              key={line}
              sx={{ typography: "s2", color: "text.secondary" }}
            >
              {line}
            </Typography>
          ))}
        </Box>
      )}
      <CustomTabs value={current} onChange={(_, v) => onTab(v)} sx={TABS_SX}>
        {tabs.map((t) => (
          <Tab
            key={t.id}
            value={t.id}
            label={
              t.id === "trials" ? `${t.label} (${trials.length})` : t.label
            }
          />
        ))}
      </CustomTabs>
      {current === "trials" ? (
        trials.length > 0 ? (
          <>
            {/* Another epoch starts from its own folds, never this one's. */}
            <CandidatesTable key={fix.id} fix={fix} />
            {/* Every candidate's target pass rate, until the design's
                All measurements view replaces it. */}
            <Box sx={{ mt: 2 }}>
              <TrialChart candidates={trials} />
            </Box>
          </>
        ) : (
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            No candidates ran.
          </Typography>
        )
      ) : (
        renderTab?.(current)
      )}
      {/* Kept mounted and toggled, as on the run page, so the drawer slides
          in and out (MUI skips the transition for a drawer mounted open). */}
      {ending && fix.source_run_id && (
        <LaunchFixDialog
          open={launching}
          envId={fix.environment_id}
          sourceRunId={fix.source_run_id}
          onClose={() => setLaunching(false)}
        />
      )}
    </Box>
  );
}

FixFinishedView.propTypes = {
  fix: PropTypes.object.isRequired,
  tab: PropTypes.string,
  onTab: PropTypes.func,
  renderTab: PropTypes.func,
};
