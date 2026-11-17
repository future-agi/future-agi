import { Button } from "@mui/material";
import React from "react";
import { useExperimentDetailContext } from "../experiment-context";

const IndividualExperimentBarSummaryRightSection = () => {
  const { setChooseWinnerOpen } = useExperimentDetailContext();
  return (
    <Button
      variant="contained"
      color="primary"
      size="small"
      onClick={() => setChooseWinnerOpen(true)}
    >
      Choose winner
    </Button>
  );
};

export default IndividualExperimentBarSummaryRightSection;
