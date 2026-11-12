import { Box, Button, Divider, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useNavigate } from "react-router";
import { isDatasetTabShown } from "src/launch/launchSwitch";
import { useDatasetListQuery } from "src/sections/develop/hooks/useDatasetListQuery";

// Run your first experiment: the user's newest dataset, if they have one.
const NEWEST_DATASET = {
  page: 0,
  pageSize: 1,
  sortBy: "created_at",
  sortOrder: "desc",
  starredFirst: false,
};

const BottomAction = ({ currentLabel, setCurrentLabel }) => {
  const navigate = useNavigate();
  // Only the experiment step looks up a dataset, and only when this build
  // shows a dataset's Experiments tab (the launch switch can hide it).
  const linksToExperiments =
    currentLabel === "RunFirstExperiment" && isDatasetTabShown("experiments");
  const { data: datasetList, isSuccess } = useDatasetListQuery(NEWEST_DATASET, {
    enabled: linksToExperiments,
    // A failed lookup only keeps the Datasets button below, so it shows
    // no error message of its own.
    meta: { errorHandled: true },
  });
  const newestDataset =
    linksToExperiments && isSuccess ? datasetList?.items?.[0] : null;
  const newestDatasetId = newestDataset?.id;
  const newestDatasetName = newestDataset?.name;

  const currentAction = useMemo(() => {
    const option = {};
    switch (currentLabel) {
      case "addKeys":
        option.buttonText = "Next";
        option.buttonAction = () => setCurrentLabel("createFirstDataset");
        break;
      case "SetupObsabilityInApplication":
        option.buttonText = "Go to Tracing";
        option.buttonAction = () => navigate("/dashboard/observe");
        break;
      case "RunFirstExperiment":
        if (newestDatasetId) {
          // Experiments run on a dataset; this opens the newest one's tab.
          option.buttonText = "Go to Experiments";
          option.hint = newestDatasetName
            ? `Opens the Experiments tab of your newest dataset, ${newestDatasetName}.`
            : "Opens the Experiments tab of your newest dataset.";
          option.buttonAction = () =>
            navigate(`/dashboard/develop/${newestDatasetId}?tab=experiments`);
        } else {
          // No dataset yet, or the list is loading or failed: the Datasets
          // list. The step above already says what to do there, so no hint.
          option.buttonText = "Go to Datasets";
          option.buttonAction = () => navigate("/dashboard/develop");
        }
        break;
      case "inviteTeamMembers":
        option.buttonText = "See summary";
        // The invite form's own submit stays the step's one filled button.
        option.variant = "outlined";
        option.buttonAction = () => setCurrentLabel("completed");
        break;
      default:
        option.buttonText = "";
        option.buttonAction = () => {};
    }
    return option;
  }, [
    currentLabel,
    navigate,
    setCurrentLabel,
    newestDatasetId,
    newestDatasetName,
  ]);

  return (
    <Box sx={{ bgcolor: "background.paper" }}>
      <Divider orientation="horizontal"></Divider>
      <Box
        px={"16px"}
        py={"12px"}
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          gap: "16px",
          height: "100%",
        }}
      >
        {currentAction.hint && (
          <Typography
            variant="s2"
            fontWeight={"fontWeightRegular"}
            color="text.secondary"
            sx={{ overflowWrap: "anywhere" }}
          >
            {currentAction.hint}
          </Typography>
        )}
        <Button
          color="primary"
          variant={currentAction.variant ?? "contained"}
          onClick={currentAction.buttonAction}
          sx={{ minWidth: "120px" }}
        >
          {currentAction?.buttonText}
        </Button>
      </Box>
    </Box>
  );
};

export default BottomAction;

BottomAction.propTypes = {
  setCurrentLabel: PropTypes.func,
  currentLabel: PropTypes.string,
};
