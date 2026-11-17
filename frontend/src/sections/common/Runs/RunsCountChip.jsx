// W1-1 kept runs: the "2 runs" chip on a leaderboard run whose config has
// run more than once. A click shows that config's earlier runs in place (or
// hides them again); the URL `every` keeps the choice.
import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { useUrlState } from "src/routes/hooks/use-url-state";
import {
  runsChipOf,
  toggleConfig,
} from "src/sections/develop-detail/ExperimentTab/attemptsModel";

export default function RunsCountChip({ run }) {
  const [every, setEvery, removeEvery] = useUrlState("every", "");
  const chip = runsChipOf(run, every);
  if (!chip) return null;
  return (
    <CustomTooltip show title={chip.tooltip} placement="top" arrow size="small">
      <Box
        component="button"
        type="button"
        data-testid="runs-count-chip"
        data-row-link
        aria-pressed={chip.open}
        onClick={(e) => {
          e.stopPropagation();
          const next = toggleConfig(every, chip.key);
          if (next) setEvery(next);
          else removeEvery();
        }}
        sx={{
          px: 0.75,
          height: 20,
          border: "1px solid",
          borderColor: chip.open ? "primary.main" : "divider",
          borderRadius: 0.5,
          bgcolor: chip.open ? "action.selected" : "background.paper",
          color: "text.secondary",
          typography: "s3",
          cursor: "pointer",
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}
      >
        {chip.text}
      </Box>
    </CustomTooltip>
  );
}
RunsCountChip.propTypes = { run: PropTypes.object };
