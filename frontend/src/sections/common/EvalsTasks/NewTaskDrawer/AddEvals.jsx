import {
  Box,
  Divider,
  IconButton,
  Typography,
  CircularProgress,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import CustomTooltip from "src/components/tooltip";
import AddedEvaluationCard from "src/sections/develop-detail/Common/AddEvaluation/AddedEvaluationCard";
import EvaluationPresetCard from "src/sections/develop-detail/Common/EvaluationPresetCard";
import { camelCaseToTitleCase } from "src/utils/utils";

const AddEvals = ({
  onCreateEvalClick,
  configuredEvals,
  onRemoveEval,
  isProjectSelected,
  isLoading,
}) => {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <CustomTooltip
        show={!isProjectSelected}
        title="To access the Evaluation section, please select a project first."
        placement="top"
        arrow
      >
        <Box>
          <EvaluationPresetCard
            onClick={isProjectSelected ? onCreateEvalClick : undefined}
            title="+ Preset Evals"
            subTitle="Select from our preset evaluation metrics or configure your own evaluation metrics"
            sx={{
              opacity: isProjectSelected ? 1 : 0.5,
              pointerEvents: isProjectSelected ? "auto" : "none",
            }}
          />
        </Box>
      </CustomTooltip>
      <CustomTooltip
        show={!isProjectSelected}
        title="To access the Evaluation section, please select a project first."
        placement="top"
        arrow
      >
        <Box>
          <EvaluationPresetCard
            onClick={isProjectSelected ? onCreateEvalClick : undefined}
            title="+ Previously Configured Evals"
            subTitle="Choose from the list of previously configured evaluation metrics"
            sx={{
              opacity: isProjectSelected ? 1 : 0.5,
              pointerEvents: isProjectSelected ? "auto" : "none",
            }}
          />
        </Box>
      </CustomTooltip>
      <Divider />
      <Box
        sx={{
          position: "relative",
          minHeight: 100,
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
        }}
      >
        <Typography fontWeight={500} fontSize="13px" color="text.primary">
          Added Evaluations
        </Typography>
        <Box
          sx={{
            marginTop: 3,
            display: "flex",
            flexDirection: "column",
            minHeight: "100px",
            gap: 1,
          }}
        >
          {isLoading ? (
            <CircularProgress size={24} />
          ) : (
            configuredEvals.map((e, index) => (
              <AddedEvaluationCard
                key={e?.id}
                description={e?.evalTemplate?.description}
                name={e?.name}
                onRemove={() => onRemoveEval(index)}
                requiredKeys={Object.keys(e?.mapping || {})
                  .map((k) => camelCaseToTitleCase(k))
                  .join(", ")}
                viewConfig={{
                  checkbox: false,
                  run: false,
                }}
              />
            ))
          )}

          <ShowComponent condition={configuredEvals.length === 0}>
            <IconButton
              size="small"
              disabled
              sx={{
                color: "text.disabled",
                width: "35px",
                position: "absolute",
                top: "45%",
                left: "50%",
                transform: "translate(-50%, -50%)",
                backgroundColor: "background.paper",
              }}
            >
              <Iconify icon="solar:close-linear" width={30} />
            </IconButton>
            <Typography
              fontWeight="600"
              fontSize="13px"
              color="text.disabled"
              sx={{
                position: "absolute",
                top: "calc(45% + 30px)",
                left: "50%",
                transform: "translateX(-50%)",
                textAlign: "center",
              }}
            >
              No Evaluations Added
            </Typography>
          </ShowComponent>
        </Box>
      </Box>
    </Box>
  );
};

AddEvals.propTypes = {
  onCreateEvalClick: PropTypes.func.isRequired,
  configuredEvals: PropTypes.array,
  onRemoveEval: PropTypes.func,
  isProjectSelected: PropTypes.bool,
  isLoading: PropTypes.any,
};

export default AddEvals;
