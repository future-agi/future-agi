import React from "react";
import { Box, Typography, Button, useTheme } from "@mui/material";
import SvgColor from "src/components/svg-color";
import SavedEvalsSkeleton from "../../EvaluationDrawer/SavedEvalsSkeleton";
import PropTypes from "prop-types";
import SavedEvalsList from "../../EvaluationDrawer/SavedEvalsList";
import CustomTooltip from "src/components/tooltip";
import { ShowComponent } from "src/components/show";
import { useEvaluationContext } from "../../EvaluationDrawer/context/EvaluationContext";

const EvaluationSection = ({
  selected,
  savedEvals,
  isEvalsLoading,
  onRemoveEval,
  onEditEvalClick,
  disabledMessage,
  isProjectEvals,
  showRun,
  hideStatus,
}) => {
  const theme = useTheme();

  savedEvals?.forEach((evalObj) => {
    if (evalObj?.mapping) {
      const eval_required_keys = Array.from(
        new Set(Object.keys(evalObj.mapping)),
      );
      evalObj.eval_required_keys = eval_required_keys;
    }
  });

  const { setVisibleSection, setCurrentTab } = useEvaluationContext();

  return (
    <>
      <ShowComponent condition={isEvalsLoading}>
        <Box
          borderRadius="4px"
          p={theme.spacing(1.5)}
          flexGrow={1}
          display="flex"
          flexDirection="column"
          justifyContent="center"
          alignItems="center"
          minHeight={"30vh"}
          sx={{ height: "100%" }}
        >
          <SavedEvalsSkeleton />
        </Box>
      </ShowComponent>

      <ShowComponent
        condition={!isEvalsLoading && (!savedEvals || savedEvals.length === 0)}
      >
        <Box
          border="1px solid"
          borderColor={theme.palette.divider}
          borderRadius={1}
          p={theme.spacing(4)}
          flexGrow={1}
          display="flex"
          flexDirection="column"
          justifyContent="center"
          alignItems="center"
          minHeight={"30vh"}
          sx={{ height: "100%" }}
        >
          <Typography fontSize={16} fontWeight="600">
            No evaluations added
          </Typography>
          <Typography
            fontSize={12}
            color={theme.palette.text.disabled}
            mb={theme.spacing(2)}
            sx={{
              textAlign: "center",
            }}
          >
            Select and configure the evals to run in your dataset
          </Typography>

          <CustomTooltip
            show={true}
            title={disabledMessage}
            placement="top"
            arrow
            disableHoverListener={selected}
          >
            <span>
              <Button
                variant="outlined"
                size="small"
                onClick={() => {
                  setCurrentTab("evals");
                  setVisibleSection("config");
                }}
                startIcon={
                  <SvgColor
                    width={16}
                    src="/assets/icons/action_buttons/ic_add.svg"
                    sx={{ color: "text.secondary" }}
                  />
                }
                disabled={!selected}
              >
                Add Evaluations
              </Button>
            </span>
          </CustomTooltip>
        </Box>
      </ShowComponent>

      <ShowComponent
        condition={!isEvalsLoading && savedEvals && savedEvals.length > 0}
      >
        <Box
          border="1px solid"
          borderColor={theme.palette.divider}
          borderRadius="4px"
          p={theme.spacing(1.5)}
          flexGrow={1}
          display="flex"
          flexDirection="column"
          justifyContent="center"
          alignItems="center"
          sx={{ height: "calc(100% - 90px)" }}
        >
          <SavedEvalsList
            evals={savedEvals}
            isProjectEvals={isProjectEvals}
            onDeleteEvalClick={(evalItem) => onRemoveEval(evalItem)}
            onEditEvalClick={onEditEvalClick}
            showRun={showRun}
            hideStatus={hideStatus}
          />
        </Box>
      </ShowComponent>
    </>
  );
};

EvaluationSection.propTypes = {
  selected: PropTypes.bool,
  savedEvals: PropTypes.array,
  isEvalsLoading: PropTypes.bool,
  onRemoveEval: PropTypes.func,
  onEditEvalClick: PropTypes.func,
  disabledMessage: PropTypes.string,
  isProjectEvals: PropTypes.bool,
  showRun: PropTypes.bool,
  hideStatus: PropTypes.bool,
};

export default EvaluationSection;
