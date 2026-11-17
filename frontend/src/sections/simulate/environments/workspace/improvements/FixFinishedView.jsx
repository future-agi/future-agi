import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  Tab,
  TextField,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";

import CopyField from "../../components/CopyField";
import FixVerdict from "./FixVerdict";
import FixDecisionDrawer from "./FixDecisionDrawer";
import RetryButton from "./RetryButton";
import ShipButton from "./ShipButton";
import TrialChart from "./TrialChart";
import TrialsTable from "./TrialsTable";
import {
  fixEnding,
  outcomeLabel,
  reviewTrialOf,
  shipAnywayLine,
  shipAnywayTrials,
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

const NOTE_SX = {
  mb: 2,
  p: 2,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
};

// The engine's own text for an ending, shown on request, to copy for support.
function EndingDetails({ text }) {
  const [open, setOpen] = useState(false);
  return (
    <Box sx={{ mt: 1 }}>
      <Button
        size="small"
        aria-expanded={open}
        endIcon={
          <Iconify
            icon={
              open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
            }
            width={14}
          />
        }
        onClick={() => setOpen((was) => !was)}
      >
        Details
      </Button>
      {open && (
        <Box sx={{ mt: 1 }}>
          <CopyField value={text} wrap />
        </Box>
      )}
    </Box>
  );
}
EndingDetails.propTypes = { text: PropTypes.string.isRequired };

// Screen 3: a finished fix. A ready fix leads with its verdict; any other
// ending says why it ended and what to do next. Every trial's change and
// results stay open to review, and a trial only its must-pass check refused
// ships on a reason.
export default function FixFinishedView({
  fix,
  tab,
  onTab,
  renderTab,
  reviewing,
  onPick,
}) {
  const ready = fix.state === "ready" && !!winnerOf(fix);
  const trials = fix.candidates || [];
  const ending = fixEnding(fix);
  const tabs =
    ready || trials.length > 0
      ? TABS
      : TABS.filter((t) => t.id === "trials" || t.id === "cost");
  const current = tabs.find((t) => t.id === tab) ? tab : "trials";
  const picking = !ready && (current === "changes" || current === "scenarios");
  return (
    <Box data-testid="fix-finished-view" sx={{ px: 3, py: 2.5 }}>
      {ready ? (
        <FixVerdict fix={fix} />
      ) : (
        ending && (
          <Box sx={NOTE_SX}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              {ending.text}
            </Typography>
            {ending.details && <EndingDetails text={ending.details} />}
          </Box>
        )
      )}
      {shipAnywayTrials(fix).map((c) => (
        <Stack
          key={c.id}
          data-testid={`ship-anyway-${c.attempt}`}
          direction={{ xs: "column", sm: "row" }}
          spacing={{ xs: 1.25, sm: 2 }}
          alignItems={{ xs: "flex-start", sm: "center" }}
          sx={NOTE_SX}
        >
          <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
            {shipAnywayLine(fix, c)}
          </Typography>
          <ShipButton fix={fix} candidate={c} />
        </Stack>
      ))}
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
        <>
          {picking && (
            <TextField
              select
              size="small"
              label="Trial"
              value={reviewing || reviewTrialOf(fix)?.id || ""}
              onChange={(e) => onPick?.(e.target.value)}
              sx={{ minWidth: 200, mb: 2 }}
            >
              {[...trials]
                .sort((a, b) => a.attempt - b.attempt)
                .map((c) => (
                  <MenuItem key={c.id} value={c.id}>
                    {`Trial ${c.attempt} · ${outcomeLabel(c.outcome, false)}`}
                  </MenuItem>
                ))}
            </TextField>
          )}
          {renderTab?.(current)}
        </>
      )}
    </Box>
  );
}

FixFinishedView.propTypes = {
  fix: PropTypes.object.isRequired,
  tab: PropTypes.string,
  onTab: PropTypes.func,
  renderTab: PropTypes.func,
  // The trial the Changes and Per scenario tabs show, and how to pick another.
  reviewing: PropTypes.string,
  onPick: PropTypes.func,
};
