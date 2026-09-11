import { Box, Button, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { format } from "date-fns";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import { convertKeysToSnakeCase } from "./common";
import { useDatasetOptimizationStoreShallow } from "./states";

const DatasetOptimizationCancelled = ({ optimization }) => {
  const { setRerunDefaultValues, setIsCreateDrawerOpen } =
    useDatasetOptimizationStoreShallow((state) => ({
      setRerunDefaultValues: state.setRerunDefaultValues,
      setIsCreateDrawerOpen: state.setIsCreateDrawerOpen,
    }));

  const handleRerun = () => {
    const {
      optimiserName,
      optimizerModelId,
      optimiserType,
      configuration,
      columnId,
      columnName,
      userEvalTemplates,
    } = optimization || {};

    const snakeCaseConfig = convertKeysToSnakeCase(configuration);
    const rerunName = `${optimiserName} - Rerun - ${format(new Date(), "dd MMM yyyy, HH:mm")}`;

    setRerunDefaultValues({
      name: rerunName,
      optimizer_model_id: optimizerModelId,
      optimizer_algorithm: optimiserType,
      optimizer_config: snakeCaseConfig,
      column_id: columnId,
      column_name: columnName,
      userEvalTemplateIds: userEvalTemplates || [],
    });
    setIsCreateDrawerOpen(true);
  };

  return (
    <Box
      sx={{
        height: "100%",
        width: "100%",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <Stack alignItems={"center"} gap={2}>
        <Box
          sx={{
            width: 47,
            height: 47,
            borderRadius: "50%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: "black.o5",
            flexShrink: 0,
          }}
        >
          <SvgColor
            sx={{ color: "red.500", width: 22 }}
            src="/assets/icons/ic_failed.svg"
          />
        </Box>
        <Typography typography={"m3"} fontWeight={"fontWeightMedium"}>
          Optimization Stopped
        </Typography>
        <Typography
          textAlign={"center"}
          typography={"s1"}
          width={"270px"}
          fontWeight={"fontWeightRegular"}
        >
          The run was stopped before completion. Click below to start it again.
        </Typography>
        <CustomTooltip
          show={Boolean(optimization?.modelDeprecated)}
          title="Model is no longer available. Select a new model to rerun."
        >
          <span>
            <Button
              variant="outlined"
              disabled={optimization?.modelDeprecated}
              onClick={handleRerun}
              startIcon={
                <SvgColor
                  sx={{ width: 20, height: 20 }}
                  src="/assets/icons/ic_refresh.svg"
                />
              }
            >
              Re-Run Optimization
            </Button>
          </span>
        </CustomTooltip>
      </Stack>
    </Box>
  );
};

DatasetOptimizationCancelled.propTypes = {
  optimization: PropTypes.object,
};

export default DatasetOptimizationCancelled;
