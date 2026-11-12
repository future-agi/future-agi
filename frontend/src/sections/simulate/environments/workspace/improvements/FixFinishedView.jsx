import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Tab, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";

import FixVerdict from "./FixVerdict";
import FixDecisionDrawer from "./FixDecisionDrawer";
import LaunchFixDialog from "./LaunchFixDialog";
import RetryButton from "./RetryButton";
import ShipButton from "./ShipButton";
import TrialChart from "./TrialChart";
import TrialsTable from "./TrialsTable";
import {
  flakyProtectedText,
  headline,
  noSafeNumbers,
  winnerOf,
} from "./fixRunView";

// The header actions of a finished fix: how it was decided, the winner's
// changes, and Ship.
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
      <RetryButton fix={fix} />
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
  { id: "scenarios", label: "Per scenario" },
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
    <Box sx={{ mt: 0.5 }}>
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
function WhatToTryNext({ fix, onTab }) {
  const [deciding, setDeciding] = useState(false);
  const [launching, setLaunching] = useState(false);
  const tried = (fix.candidates || []).length > 0;
  const line = { typography: "s2", color: "text.secondary" };
  return (
    <Box data-testid="what-to-try-next" sx={{ mb: 2 }}>
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
            <Button size="small" onClick={() => setLaunching(true)}>
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
      {launching && (
        <LaunchFixDialog
          open
          envId={fix.environment_id}
          sourceRunId={fix.source_run_id}
          onClose={() => setLaunching(false)}
        />
      )}
    </Box>
  );
}
WhatToTryNext.propTypes = {
  fix: PropTypes.object.isRequired,
  onTab: PropTypes.func,
};

// Screen 3: a finished fix. A ready fix leads with its verdict; any other
// ending says why it ended, and nothing ships.
export default function FixFinishedView({ fix, tab, onTab, renderTab }) {
  const ready = fix.state === "ready" && !!winnerOf(fix);
  const trials = fix.candidates || [];
  const noSafe = fix.state === "no_improvement";
  const ending = headline(fix);
  const tabs = ready
    ? TABS
    : TABS.filter((t) => t.id === "trials" || t.id === "cost");
  const current = tabs.find((t) => t.id === tab) ? tab : "trials";
  const flaky = flakyProtectedText(fix);
  return (
    <Box data-testid="fix-finished-view" sx={{ px: 3, py: 2.5 }}>
      {ready ? (
        <FixVerdict fix={fix} />
      ) : (
        ending && (
          <Box
            sx={{
              mb: 2,
              p: 2,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
            }}
          >
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              {ending}
            </Typography>
            {noSafe && <NoSafeNumbers fix={fix} />}
          </Box>
        )
      )}
      {flaky && (
        <Typography
          data-testid="flaky-protected"
          sx={{ typography: "s2", color: "text.secondary", mb: 2 }}
        >
          {flaky}
        </Typography>
      )}
      {noSafe && <WhatToTryNext fix={fix} onTab={onTab} />}
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
            <TrialChart candidates={trials} />
            <TrialsTable fix={fix} />
          </>
        ) : (
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            No candidates ran.
          </Typography>
        )
      ) : (
        renderTab?.(current)
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
