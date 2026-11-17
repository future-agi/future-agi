import React from "react";
import PropTypes from "prop-types";
import { IconButton } from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { runResultHref } from "./runsModel";

/** Open the result / trajectory (icon buttons). */
const RunLinks = ({ run, rowId, datasetId }) => {
  const navigate = useNavigate();
  const href = runResultHref(run, rowId, { datasetId });
  if (!href) return null;
  return (
    <>
      {["agent", "graph"].includes(run.target_type) && (
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
              navigate(
                runResultHref(run, rowId, { trajectory: true, datasetId }),
              )
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
        title={
          run.experiment_id
            ? "Open this result in the experiment"
            : "Open this result"
        }
      >
        <IconButton
          size="small"
          aria-label={`Open ${run.label}`}
          onClick={() => navigate(href)}
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
  datasetId: PropTypes.string,
};

export default RunLinks;
