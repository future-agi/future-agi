import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Tab, Typography } from "@mui/material";

import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";

import FixVerdict from "./FixVerdict";
import FixDecisionDrawer from "./FixDecisionDrawer";
import RetryButton from "./RetryButton";
import ShipButton from "./ShipButton";
import TrialChart from "./TrialChart";
import TrialsTable from "./TrialsTable";
import { headline, noSafeNumbers, winnerOf } from "./fixRunView";

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
          {`Review Trial ${winner.attempt}`}
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
  { id: "trials", label: "Trials" },
  { id: "changes", label: "Changes" },
  { id: "scenarios", label: "Per scenario" },
  { id: "cost", label: "Cost" },
];

// What the no-safe headline rests on, behind a toggle: each trial's rejecting check as
// measured, and the engine's own summary word for word.
function NoSafeNumbers({ fix }) {
  const [open, setOpen] = useState(false);
  const { trials, engineNote } = noSafeNumbers(fix);
  if (!trials.length && !engineNote) return null;
  const line = { typography: "s3", color: "text.secondary" };
  return (
    <Box sx={{ mt: 0.5 }}>
      <Button size="small" onClick={() => setOpen((o) => !o)} sx={{ px: 0 }}>
        {open ? "Hide the numbers" : "Show the numbers"}
      </Button>
      {open && (
        <Stack spacing={0.5} sx={{ mt: 0.5 }} data-testid="no-safe-numbers">
          {trials.map((t) => (
            <Typography key={t.attempt} sx={line}>
              {`Trial ${t.attempt} · ${t.label}: ${t.detail}`}
            </Typography>
          ))}
          {engineNote && (
            <Typography sx={line}>{`Engine's note: ${engineNote}`}</Typography>
          )}
        </Stack>
      )}
    </Box>
  );
}
NoSafeNumbers.propTypes = { fix: PropTypes.object.isRequired };

// Screen 3: a finished fix. A ready fix leads with its verdict; any other
// ending says why it ended, and nothing ships.
export default function FixFinishedView({ fix, tab, onTab, renderTab }) {
  const ready = fix.state === "ready" && !!winnerOf(fix);
  const trials = fix.candidates || [];
  const noSafe = fix.state === "no_improvement";
  const ending = noSafe
    ? headline(fix)
    : fix.failure?.message || fix.report?.summary;
  const tabs = ready
    ? TABS
    : TABS.filter((t) => t.id === "trials" || t.id === "cost");
  const current = tabs.find((t) => t.id === tab) ? tab : "trials";
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
            No trials ran.
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
