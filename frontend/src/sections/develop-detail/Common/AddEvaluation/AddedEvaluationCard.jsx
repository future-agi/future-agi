import { Box, Checkbox, Chip, IconButton, Typography } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify/iconify";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import ConfirmRunEvaluations from "./ConfirmRunEvaluations";

const AddedEvaluationCard = ({
  userEval,
  selected,
  onChange,
  viewConfig,
  requiredKeys,
  model,
  name,
  description,
  onRemove,
  experimentEval,
  refreshGrid,
  showDeleteButton = true,
  isCompare = false,
  selectedDatasets = [],
  onClose = () => {},
}) => {
  const { dataset } = useParams();
  const showCheckbox = viewConfig?.checkbox ?? true;
  const showRun = viewConfig?.run ?? true;

  const { mutate: runEval, isPending: startingDatasetEval } = useMutation({
    mutationFn: (d) => axios.post(endpoints.develop.eval.runEvals(dataset), d),
    onSuccess: () => {
      enqueueSnackbar("Evaluation started successfully", {
        variant: "success",
      });
      refreshGrid(null, true);
    },
  });

  const { mutate: runCompareEval, isPending: startingCompareDatasetEval } =
    useMutation({
      mutationFn: (d) =>
        axios.post(endpoints.develop.eval.compareRunEvals(dataset), d),
      onSuccess: () => {
        enqueueSnackbar("Evaluation started successfully", {
          variant: "success",
        });
        refreshGrid(null, true);
        onClose?.();
      },
    });

  const { mutate: runExperimentEvals, isPending: startingExperimentEvals } =
    useMutation({
      mutationFn: (d) =>
        axios.post(
          endpoints.develop.experiment.runEvaluation(
            experimentEval?.experimentId,
          ),
          d,
        ),
      onSuccess: () => {
        enqueueSnackbar("Evaluation started successfully", {
          variant: "success",
        });
        refreshGrid(null, true);
      },
    });

  const startingEval =
    startingCompareDatasetEval ||
    startingDatasetEval ||
    startingExperimentEvals;

  const [confirmRunEvaluationsOpen, setConfirmRunEvaluationsOpen] =
    useState(false);

  return (
    <Box
      sx={{
        padding: "16px",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        gap: "12px",
        display: "flex",
        alignItems: "flex-start",
      }}
    >
      {showCheckbox && (
        <Checkbox
          checked={selected}
          onChange={(_, checked) => onChange(checked)}
          inputProps={{ "aria-label": "controlled" }}
          sx={{
            padding: 0,
          }}
        />
      )}
      <Box sx={{ display: "flex", flexDirection: "column", flex: 1, gap: 2 }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            width: "100%",
          }}
        >
          <Typography
            variant="body2"
            color="text.primary"
            fontWeight={600}
            fontSize="13px"
          >
            {name}
          </Typography>
          {showDeleteButton && (
            <IconButton sx={{ padding: 0 }} onClick={onRemove}>
              <Iconify
                icon="solar:trash-bin-trash-bold"
                sx={{ color: "text.secondary" }}
              />
            </IconButton>
          )}
        </Box>
        <Typography variant="caption" color="text.secondary" fontSize="12px">
          {description}
        </Typography>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 1,
          }}
        >
          <Box
            sx={{
              display: "flex",
              gap: 1,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            {requiredKeys?.length ? (
              <Chip
                label={`Required Fields - ${requiredKeys}`}
                size="small"
                variant="soft"
                color="primary"
                sx={{
                  // eslint-disable-next-line no-restricted-syntax -- ds: layout (fill row)
                  height: "100%",
                  display: "flex",
                  flexDirection: "row",
                  "& .MuiChip-label": {
                    overflowWrap: "break-word",
                    whiteSpace: "normal",
                    textOverflow: "clip",
                  },
                }}
              />
            ) : (
              <></>
            )}
            {model?.length ? (
              <Chip label={model} size="small" variant="soft" color="primary" />
            ) : (
              <></>
            )}
          </Box>
          {showRun && (
            <ConfirmRunEvaluations
              open={confirmRunEvaluationsOpen}
              onClose={() => setConfirmRunEvaluationsOpen(false)}
              onConfirm={() => {
                setConfirmRunEvaluationsOpen(false);
                if (experimentEval) {
                  //@ts-ignore
                  runExperimentEvals({ user_eval_ids: [userEval.id] });
                } else {
                  //@ts-ignore
                  if (isCompare) {
                    runCompareEval({
                      user_eval_names: [userEval.name],
                      dataset_ids: selectedDatasets,
                    });
                  } else {
                    runEval({ user_eval_ids: [userEval.id] });
                  }
                }
              }}
              selectedUserEvalList={[userEval]}
            />
          )}
          {showRun && (
            <LoadingButton
              size="small"
              variant="contained"
              color="primary"
              loading={startingEval}
              onClick={() => setConfirmRunEvaluationsOpen(true)}
            >
              Run
            </LoadingButton>
          )}
        </Box>
      </Box>
    </Box>
  );
};

AddedEvaluationCard.propTypes = {
  showDeleteButton: PropTypes.bool,
  selectedUserEvalList: PropTypes.array,
  setSelectedUserEvalList: PropTypes.func,
  userEval: PropTypes.object,
  selected: PropTypes.bool,
  onChange: PropTypes.func,
  viewConfig: PropTypes.object,
  requiredKeys: PropTypes.string,
  model: PropTypes.string,
  name: PropTypes.string,
  description: PropTypes.string,
  onRemove: PropTypes.func,
  experimentEval: PropTypes.object,
  refreshGrid: PropTypes.func,
  runVisible: PropTypes.bool,
  isCompare: PropTypes.bool,
  selectedDatasets: PropTypes.array,
  onClose: PropTypes.func,
};

export default AddedEvaluationCard;
