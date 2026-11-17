// RE-01 (options-truth L3 G10): an experiment id that does not exist (or was
// deleted) is a terminal state — the page says so and leads back, instead of
// `Loading experiment…` and a progress bar that never end. Same look as
// DataTab/DatasetNotFound.jsx.
import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { Helmet } from "react-helmet-async";
import { paths } from "src/routes/paths";
import EmptyState from "src/sections/common/ux/EmptyState";
import { datasetOfReferrer } from "./experimentNotFoundModel";

export default function ExperimentNotFound({ experimentId, referrer }) {
  const datasetId = datasetOfReferrer(
    referrer ?? (typeof document !== "undefined" ? document.referrer : ""),
    typeof window !== "undefined" ? window.location.origin : "",
  );
  const actions = [
    ...(datasetId
      ? [
          {
            label: "Back to the dataset's experiments",
            href: `${paths.dashboard.develop}/${datasetId}?tab=experiments`,
            icon: "mdi:arrow-left",
          },
        ]
      : []),
    {
      label: "All datasets",
      href: paths.dashboard.develop,
      icon: datasetId ? "mdi:database-outline" : "mdi:arrow-left",
    },
  ];
  return (
    <Box
      data-testid="experiment-not-found"
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: "background.paper",
        p: 3,
      }}
    >
      <Helmet>
        <title>Experiment not found · Future AGI</title>
      </Helmet>
      <EmptyState
        icon="mdi:flask-off-outline"
        title="Experiment not found"
        body={`No experiment has the id ${experimentId}. It may have been deleted, or the link is wrong.`}
        actions={actions}
      />
    </Box>
  );
}
ExperimentNotFound.propTypes = {
  experimentId: PropTypes.string,
  referrer: PropTypes.string,
};
