import PropTypes from "prop-types";
import { Box, Tooltip } from "@mui/material";
import { useTheme } from "@mui/material/styles";

import Iconify from "src/components/iconify";
import { statusColors } from "src/utils/runStatus";

import { epochIcon } from "./issueEpochView";

// The issue list's epoch mark: one small icon after the name, its state on hover.
// An issue with no epoch shows nothing.
export default function EpochIssueIcon({ status }) {
  const theme = useTheme();
  const mark = epochIcon(status);
  if (!mark) return null;
  return (
    <Tooltip title={mark.tip} arrow placement="top">
      <Box
        component="span"
        aria-label={mark.tip}
        data-testid="issue-epoch-icon"
        sx={{
          display: "inline-flex",
          flexShrink: 0,
          color: statusColors(theme, mark.role).solid,
        }}
      >
        <Iconify icon={mark.icon} width={14} />
      </Box>
    </Tooltip>
  );
}

EpochIssueIcon.propTypes = { status: PropTypes.object };
