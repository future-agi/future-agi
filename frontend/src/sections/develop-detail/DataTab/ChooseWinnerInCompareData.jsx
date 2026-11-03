import {
  Box,
  Button,
  Drawer,
  IconButton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { useForm } from "react-hook-form";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import WeightSlider from "src/sections/project/ChooseWinner/WeightSlider";
import WeightSliderSection from "src/sections/project/ChooseWinner/WeightSliderSection";
import { enqueueSnackbar } from "notistack";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const ChooseProjectExperimentWinnerDrawerChild = ({
  onClose,
  evalsData,
  selectedDatasets,
  baseColumn,
  commonColumn,
  setIsChooseWinnerSelected,
  setDataAfterChooseWinner,
}) => {
  const theme = useTheme();

  // Create default values with unique field names based on data names

  const { mutate: handleSubmitWinnerSettings } = useMutation({
    mutationFn: (weights) =>
      axios.post(endpoints.dataset.getSummaryTable(selectedDatasets[0]), {
        dataset_ids: selectedDatasets.slice(1),
        base_column_name: baseColumn,
        // dataset_info: datasetInfo,
        common_column_names: commonColumn,
        is_winner_chosen: true,
        weights: weights,
      }),
    onSuccess: (data) => {
      setIsChooseWinnerSelected(true);
      setDataAfterChooseWinner(data);
      onClose();
      enqueueSnackbar("Winner chosen successfully", {
        variant: "success",
      });
    },
  });

  const getDefaultValues = () => {
    const defaultValues = { weights: {} };
    evalsData.forEach((data) => {
      defaultValues.weights[data.name] = 0; // Default value of 0 for each slider
    });
    return defaultValues;
  };

  const { control, handleSubmit } = useForm({
    defaultValues: getDefaultValues(),
  });

  return (
    <Box
      sx={{
        // padding: "20px",
        width: "550px",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        gap: 2,
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: theme.spacing(2, 2.5, 0),
        }}
      >
        <Typography fontWeight={600} fontSize="16px" color="text.primary">
          Winner Settings
        </Typography>
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </Box>
      <Typography
        variant="caption"
        sx={{ marginTop: 0, padding: theme.spacing(0, 2.5, 0) }}
        color="text.secondary"
        fontSize="13px"
        fontWeight={400}
      >
        Choose the overall importance value of the given variable below to
        determine which dataset performed the best
      </Typography>
      <Stack
        direction={"column"}
        sx={{
          flex: 1,
        }}
      >
        <Typography
          sx={{
            fontSize: "13px",
            fontWeight: 500,
            color: "text.primary",
            padding: theme.spacing(2, 2.5, 0),
          }}
        >
          Evaluation Metrics
        </Typography>
        <Box
          sx={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            gap: evalsData?.length > 1 ? "66px" : "40px",
            overflow: "auto",
            padding: theme.spacing(0, 2.5, 0),
          }}
        >
          {evalsData?.map((data, index) => (
            <WeightSliderSection key={index}>
              <WeightSlider
                control={control}
                label={data?.name}
                fieldName={`weights.${data.name}`} // Use unique field name for each slider
              />
            </WeightSliderSection>
          ))}
        </Box>
      </Stack>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          padding: theme.spacing(0, 2.5, 2),
        }}
      >
        <Button variant="outlined" size="small" fullWidth onClick={onClose}>
          Cancel
        </Button>
        <LoadingButton
          color="primary"
          size="small"
          variant="contained"
          fullWidth
          // loading={isPending}
          onClick={handleSubmit((data) => {
            handleSubmitWinnerSettings(data?.weights);
          })}
        >
          Run Text
        </LoadingButton>
      </Box>
    </Box>
  );
};

ChooseProjectExperimentWinnerDrawerChild.propTypes = {
  setIsChooseWinnerSelected: PropTypes.func,
  setDataAfterChooseWinner: PropTypes.func,
  commonColumn: PropTypes.array,
  datasetInfo: PropTypes.array,
  selectedDatasets: PropTypes.array,
  baseColumn: PropTypes.string,
  evalsData: PropTypes.array,
  onClose: PropTypes.func,
  columns: PropTypes.array,
  refreshGrid: PropTypes.func,
};

const ChooseWinnerInCompareData = ({
  open,
  onClose,
  columns,
  refreshGrid,
  evalsData,
  selectedDatasets,
  baseColumn,
  commonColumn,
  datasetInfo,
  setIsChooseWinnerSelected,
  setDataAfterChooseWinner,
}) => {
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      sx={coveredDrawerSx(covered)}
      // onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 99,
          borderRadius: "8px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <ChooseProjectExperimentWinnerDrawerChild
        evalsData={evalsData}
        onClose={onClose}
        columns={columns}
        refreshGrid={refreshGrid}
        selectedDatasets={selectedDatasets}
        baseColumn={baseColumn}
        commonColumn={commonColumn}
        datasetInfo={datasetInfo}
        setDataAfterChooseWinner={setDataAfterChooseWinner}
        setIsChooseWinnerSelected={setIsChooseWinnerSelected}
      />
    </Drawer>
  );
};

ChooseWinnerInCompareData.propTypes = {
  setDataAfterChooseWinner: PropTypes.func,
  setIsChooseWinnerSelected: PropTypes.func,
  datasetInfo: PropTypes.array,
  commonColumn: PropTypes.array,
  selectedDatasets: PropTypes.array,
  baseColumn: PropTypes.string,
  evalsData: PropTypes.array,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  columns: PropTypes.array,
  refreshGrid: PropTypes.func,
};

export default ChooseWinnerInCompareData;
