import React, { useContext } from "react";
import PropTypes from "prop-types";
import { Link, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import CustomTooltip from "src/components/tooltip";
import { classicExperimentPath } from "./classicExperimentPath";
import { OTHER_EXPERIMENTS_COPY as COPY } from "./otherExperimentsCopy";
import { OtherExperimentsContext } from "./otherExperimentsContext";

/**
 * On a run row of the runs view: when the run's experiment is listed under
 * "Other experiments" (the runs view cannot show all of it), which experiment
 * the run belongs to, as a link that opens it at the page that group's row
 * opens, where all of its variants show. The run itself stays in the list.
 * Nothing for any other run, or where the tab lists no such group.
 */
export default function OtherExperimentRunLink({
  experimentId,
  datasetId,
  fallbackName,
}) {
  const { byId } = useContext(OtherExperimentsContext);
  const row = byId?.[String(experimentId)];
  if (!row || !datasetId) return null;
  const name = row.name ?? fallbackName ?? "";
  const to = classicExperimentPath({ id: row.id, name }, datasetId);
  return (
    <CustomTooltip
      show
      title={COPY.runExperimentTip}
      size="small"
      placement="top"
      arrow
    >
      <Typography
        component="span"
        typography="s3"
        color="text.secondary"
        noWrap
        data-testid="other-experiment-run-link"
        sx={{ minWidth: 0, flexShrink: 1 }}
      >
        {`${COPY.runExperimentPrefix} `}
        <Link
          component={RouterLink}
          to={to}
          underline="hover"
          data-row-link
          onClick={(e) => e.stopPropagation()}
        >
          {name}
        </Link>
      </Typography>
    </CustomTooltip>
  );
}

OtherExperimentRunLink.propTypes = {
  experimentId: PropTypes.string,
  datasetId: PropTypes.string,
  fallbackName: PropTypes.string,
};
