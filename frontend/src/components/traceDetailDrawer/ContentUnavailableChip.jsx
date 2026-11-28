import React from "react";
import PropTypes from "prop-types";
import { Chip, Tooltip } from "@mui/material";
import Iconify from "../iconify";

const SIDE_LABELS = { input: "input", output: "output" };

// Shown when the API reports `content_unavailable` for a span: the stored
// preview is displayed because the full input/output could not be read.
const ContentUnavailableChip = ({ sides }) => {
  const known = (Array.isArray(sides) ? sides : []).filter(
    (side) => SIDE_LABELS[side],
  );
  if (known.length === 0) {
    return null;
  }
  const which = known.map((side) => SIDE_LABELS[side]).join(" and ");
  return (
    <Tooltip
      title={`The full ${which} of this span could not be loaded. The text shown is a shortened preview.`}
      arrow
    >
      <Chip
        size="small"
        color="warning"
        variant="outlined"
        icon={<Iconify icon="solar:danger-triangle-linear" width={14} />}
        label="Full content unavailable — preview shown"
        data-testid="content-unavailable-chip"
        sx={{ height: "22px" }}
      />
    </Tooltip>
  );
};

ContentUnavailableChip.propTypes = {
  sides: PropTypes.arrayOf(PropTypes.string),
};

export default ContentUnavailableChip;
