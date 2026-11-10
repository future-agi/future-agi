import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { BUCKETS } from "./scenarioReadiness";

const MARKS = {
  blocked: {
    label: "Blocked",
    tip: "Held back from runs with the default versions: a check it needs is blocked. See Ready to run in the run plan.",
  },
  stale: {
    label: "Not proved here",
    tip: "Not proved on this environment version yet. It still runs.",
  },
  untested: {
    label: "Not checked",
    tip: "What it needs couldn't be read, so it isn't checked. It still runs.",
  },
};

export default function ReadinessMark({ status }) {
  const mark = MARKS[status];
  if (!mark) return null;
  return (
    <CustomTooltip show title={mark.tip} size="small" arrow>
      <Box sx={{ display: "flex", flexShrink: 0 }}>
        <StatusBadge status={status} label={mark.label} size="sm" />
      </Box>
    </CustomTooltip>
  );
}

ReadinessMark.propTypes = {
  status: PropTypes.oneOf(BUCKETS),
};
