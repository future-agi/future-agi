import React from "react";
import PropTypes from "prop-types";
import { IconButton } from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { runResultHref } from "./runsModel";

/** Open the result / trajectory (icon buttons). */
const RunLinks = ({ run, rowId }) => {
  const navigate = useNavigate();
  return (
    <>
      {run.target_type === "agent" && (
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title="Open the trajectory"
        >
          <IconButton
            size="small"
            aria-label={`Trajectory of ${run.label}`}
            onClick={() =>
              navigate(runResultHref(run, rowId, { trajectory: true }))
            }
          >
            <Iconify icon="mdi:transit-connection-variant" width={16} />
          </IconButton>
        </CustomTooltip>
      )}
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title="Open this result in the experiment"
      >
        <IconButton
          size="small"
          aria-label={`Open ${run.label}`}
          onClick={() => navigate(runResultHref(run, rowId))}
        >
          <Iconify icon="mdi:open-in-new" width={16} />
        </IconButton>
      </CustomTooltip>
    </>
  );
};

RunLinks.propTypes = {
  run: PropTypes.object.isRequired,
  rowId: PropTypes.string.isRequired,
};

export default RunLinks;
