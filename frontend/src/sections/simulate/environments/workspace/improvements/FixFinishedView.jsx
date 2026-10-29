import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Tab, Tabs, Typography } from "@mui/material";

import FixVerdict from "./FixVerdict";
import FixDecisionDrawer from "./FixDecisionDrawer";
import ShipButton from "./ShipButton";
import TrialChart from "./TrialChart";
import TrialsTable from "./TrialsTable";
import { winnerOf } from "./fixRunView";

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
          onClick={() => setDeciding(true)}
        >
          How it was decided
        </Button>
      )}
      {ready && (
        <Button
          size="small"
          variant="outlined"
          onClick={() => onReview(winner.id)}
        >
          {`Review Trial ${winner.attempt}`}
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

const TABS = [
  { id: "trials", label: "Trials" },
  { id: "changes", label: "Changes" },
  { id: "scenarios", label: "Per scenario" },
  { id: "cost", label: "Cost" },
];

// Screen 3: a finished fix. A ready fix leads with its verdict; any other
// ending says why it ended, and nothing ships.
export default function FixFinishedView({ fix, tab, onTab, renderTab }) {
  const ready = fix.state === "ready" && !!winnerOf(fix);
  const trials = fix.candidates || [];
  const ending = fix.report?.summary || fix.failure?.message;
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
          </Box>
        )
      )}
      <Tabs value={current} onChange={(_, v) => onTab(v)} sx={{ mb: 2 }}>
        {tabs.map((t) => (
          <Tab
            key={t.id}
            value={t.id}
            label={
              t.id === "trials" ? `${t.label} (${trials.length})` : t.label
            }
          />
        ))}
      </Tabs>
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
