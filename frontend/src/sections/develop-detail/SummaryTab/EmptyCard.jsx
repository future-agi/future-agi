import EmptyContent from "src/components/empty-content";
import React from "react";
import { LoadingButton } from "@mui/lab";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import logger from "src/utils/logger";

const EmptyCard = ({
  tab,
  setCurrentTab,
  onRunEvaluation,
  onRunPrompt,
  datasetId,
}) => {
  const handleAddEvaluation = () => {
    setCurrentTab("data");
    if (tab === "Evaluations") {
      logger.debug("onRunEvaluation");
      onRunEvaluation();
    } else {
      onRunPrompt();
    }
  };

  return (
    <EmptyContent
      title={
        tab === "Evaluations"
          ? "No evaluation has been added to this dataset"
          : "No prompt template has been added to this dataset"
      }
      description={
        tab === "Evaluations"
          ? "To view evaluation summary, Apply evaluations to your dataset"
          : "To view prompt summary, Add prompts to your dataset"
      }
      action={
        !datasetId ? (
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            type="submit"
            onClick={() => handleAddEvaluation()}
            startIcon={
              <Iconify icon="solar:add-linear" width="16px" height="16px" />
            }
          >
            Add {tab}
          </LoadingButton>
        ) : undefined
      }
    />
  );
};

EmptyCard.propTypes = {
  datasetId: PropTypes.any,
  dataSet: PropTypes.string,
  tab: PropTypes.string,
  setCurrentTab: PropTypes.func.isRequired,
  onRunEvaluation: PropTypes.func,
  onRunPrompt: PropTypes.func,
};
export default EmptyCard;
