import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Checkbox,
  Divider,
  FormControlLabel,
  IconButton,
  Typography,
  Alert,
} from "@mui/material";
import Iconify from "src/components/iconify";
import EvaluationPresetCard from "../Common/EvaluationPresetCard";
import AddedEvaluationCard from "../Common/AddEvaluation/AddedEvaluationCard";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import LoadingButton from "@mui/lab/LoadingButton";
import ConfirmRunEvaluations from "../Common/AddEvaluation/ConfirmRunEvaluations";
import { ShowComponent } from "src/components/show";
import { camelCaseToTitleCase } from "src/utils/utils";
import DeleteEval from "../Common/AddEvaluation/DeleteEval";
import PromptEvaluationListPage from "./PromptEvaluationListPage";
import HelperText from "../Common/HelperText";

const EvaluationListPage = ({
  onClose,
  setEvaluationTypeOpen,
  setConfigureEvalOpen,
  refreshGrid,
  datasetId,
  experimentEval,
  setSelectedEval,
  module,
  evalsConfigs,
  setEvalsConfigs,
  handleLabelsAdd,
  setFormIsDirty,
  openDrawer,
}) => {
  const hasRun = useRef(false);
  const [selectedUserEvalList, setSelectedUserEvalList] = useState([]);
  const [confirmRunEvaluationsOpen, setConfirmRunEvaluationsOpen] =
    useState(false);
  const baseColumnId = experimentEval?.baseColumnId;
  const [errorMessage, setErrorMessage] = useState(null);
  const [open, setOpen] = useState(null);
  const queryClient = useQueryClient();

  const { mutate: runDatasetEvals, isPending: startingDatasetEvals } =
    useMutation({
      mutationFn: (d) =>
        axios.post(endpoints.develop.eval.runEvals(datasetId), d),
      onSuccess: () => {
        enqueueSnackbar("Evaluations started successfully", {
          variant: "success",
        });
        refreshGrid();
        onClose();
      },
      onError: (error) => {
        setErrorMessage(
          error?.response?.data?.message || "Failed to run evaluations",
        );
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
        enqueueSnackbar("Evaluations started successfully", {
          variant: "success",
        });
        refreshGrid();
        onClose();
      },
      onError: (error) => {
        setErrorMessage(
          error?.response?.data?.message || "Failed to run evaluations",
        );
      },
    });

  const startingEvals = startingDatasetEvals || startingExperimentEvals;

  const { data: datasetUserEvalList } = useQuery({
    queryFn: () =>
      axios.get(endpoints.develop.eval.getEvalsList(datasetId), {
        params: {
          eval_type: "user",
        },
      }),
    queryKey: ["develop", "user-eval-list", datasetId],
    select: (d) => d?.data?.result?.evals,
  });

  const { data: columnConstrainedUserEvalList } = useQuery({
    queryFn: () =>
      axios.get(endpoints.develop.optimizeDevelop.columnInfo, {
        params: { column_id: baseColumnId },
      }),
    queryKey: ["experiment-column-info", baseColumnId],
    enabled: Boolean(baseColumnId),
    select: (data) => data?.data?.result,
  });

  const userEvalList = columnConstrainedUserEvalList || datasetUserEvalList;

  const refreshQueryClientQueries = () => {
    queryClient.invalidateQueries({
      queryKey: ["develop", "user-eval-list", datasetId],
    });
    queryClient.invalidateQueries({
      queryKey: ["experiment-column-info", baseColumnId],
    });
  };

  useEffect(() => {
    if (openDrawer) {
      refreshQueryClientQueries();
    }
  }, [openDrawer]);

  useEffect(() => {
    if (userEvalList?.length) {
      const updatedSelectedEvaluations = selectedUserEvalList.filter(
        (selectedEval) =>
          userEvalList.some((evaluation) => evaluation.id === selectedEval.id),
      );
      setSelectedUserEvalList(updatedSelectedEvaluations);
      setEvalsConfigs(userEvalList);
    }
    if (!hasRun.current && userEvalList?.length) {
      if (selectedUserEvalList?.length !== userEvalList.length) {
        setSelectedUserEvalList(userEvalList);
        setEvalsConfigs(userEvalList);
        hasRun.current = true;
      }
    }
  }, [userEvalList]);

  const renderAddedEvaluations = () => {
    if (module === "prompt") {
      return (
        <PromptEvaluationListPage
          evalsConfigs={evalsConfigs}
          setEvalsConfigs={setEvalsConfigs}
          handleLabelsAdd={handleLabelsAdd}
          onClose={() => {
            setFormIsDirty(false);
            onClose();
          }}
        />
      );
    }

    return (
      <>
        <Box
          sx={{
            paddingX: "20px",
            flex: 1,
            gap: "10px",
            flexDirection: "column",
            display: "flex",
            overflowY: "auto",
          }}
        >
          <Box
            sx={{
              gap: "10px",
              flexDirection: "column",
              display: "flex",
              position: "sticky",
              top: 0,
              zIndex: 1,
              backgroundColor: "background.paper",
            }}
          >
            <Typography fontWeight={600} color="text.secondary">
              Added Evaluations
            </Typography>
            <ShowComponent condition={Boolean(userEvalList?.length)}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <Typography
                  fontSize="12px"
                  variant="body2"
                  color="text.secondary"
                >
                  {selectedUserEvalList?.length} Selected
                </Typography>
                <FormControlLabel
                  sx={{
                    marginLeft: 0,
                    ".MuiFormControlLabel-label": {
                      fontSize: "12px",
                      color: "text.primary",
                    },
                  }}
                  control={
                    <Checkbox
                      checked={
                        selectedUserEvalList?.length === userEvalList?.length
                      }
                      indeterminate={
                        selectedUserEvalList?.length > 0 &&
                        selectedUserEvalList?.length < userEvalList?.length
                      }
                      onChange={(_, checked) => {
                        if (checked) {
                          setSelectedUserEvalList(userEvalList);
                        } else {
                          setSelectedUserEvalList([]);
                        }
                      }}
                      inputProps={{ "aria-label": "controlled" }}
                      sx={{
                        padding: 0,
                        paddingRight: 1,
                        "&.MuiCheckbox-indeterminate": {
                          color: "text.secondary",
                        },
                      }}
                    />
                  }
                  label="Select All"
                  labelPlacement="end"
                />
              </Box>
            </ShowComponent>
          </Box>

          <Box
            sx={{
              gap: "10px",
              flexDirection: "column",
              display: "flex",
              flex: 1,
            }}
          >
            {userEvalList?.map((eachUserEval) => (
              <AddedEvaluationCard
                key={eachUserEval.id}
                userEval={eachUserEval}
                requiredKeys={eachUserEval?.evalRequiredKeys
                  ?.map((k) => camelCaseToTitleCase(k))
                  .join(", ")}
                model={eachUserEval?.model}
                name={eachUserEval?.name}
                description={eachUserEval?.description}
                setSelectedUserEvalList={setSelectedUserEvalList}
                selectedUserEvalList={selectedUserEvalList}
                selected={Boolean(
                  selectedUserEvalList?.find((e) => e.id === eachUserEval?.id),
                )}
                experimentEval={experimentEval}
                refreshGrid={refreshGrid}
                onChange={(checked) => {
                  if (checked) {
                    setSelectedUserEvalList([
                      ...selectedUserEvalList,
                      eachUserEval,
                    ]);
                  } else {
                    setSelectedUserEvalList(
                      selectedUserEvalList.filter(
                        (e) => e.id !== eachUserEval.id,
                      ),
                    );
                  }
                }}
                onRemove={() => setOpen({ ...eachUserEval })}
              />
            ))}
            <DeleteEval
              open={Boolean(open)}
              setOpen={() => {
                setOpen(null);
              }}
              dataset={datasetId}
              refreshGrid={() => {
                setOpen(null);
                refreshGrid();
                if (open && open.id) {
                  setSelectedUserEvalList((prevList) =>
                    prevList.filter((item) => item.id !== open.id),
                  );
                }

                refreshQueryClientQueries();
              }}
              userEval={open}
            />
          </Box>
        </Box>
        <ConfirmRunEvaluations
          open={confirmRunEvaluationsOpen}
          onClose={() => setConfirmRunEvaluationsOpen(false)}
          onConfirm={() => {
            setConfirmRunEvaluationsOpen(false);
            if (experimentEval) {
              //@ts-ignore
              runExperimentEvals({
                eval_template_ids: selectedUserEvalList.map((e) => e.id),
              });
            } else {
              //@ts-ignore
              runDatasetEvals({
                user_eval_ids: selectedUserEvalList.map((e) => e.id),
              });
            }
          }}
          selectedUserEvalList={selectedUserEvalList}
        />
        <Box
          sx={{
            width: "100%",
            position: "sticky",
            bottom: "0px",
            paddingX: "20px",
            paddingY: "20px",
          }}
        >
          <LoadingButton
            variant="contained"
            color="primary"
            fullWidth
            disabled={!selectedUserEvalList?.length}
            loading={startingEvals}
            //@ts-ignore
            onClick={() => {
              setConfirmRunEvaluationsOpen(true);
              onClose();
            }}
          >
            {module === "prompt"
              ? "Add all evaluations"
              : `Run ${selectedUserEvalList?.length ? selectedUserEvalList?.length : ""} Evaluations`}
          </LoadingButton>
        </Box>
      </>
    );
  };

  return (
    <>
      <IconButton
        onClick={onClose}
        sx={{ position: "absolute", top: "12px", right: "12px" }}
      >
        <Iconify icon="solar:close-linear" />
      </IconButton>
      <Box
        sx={{
          gap: "20px",
          display: "flex",
          flexDirection: "column",
          height: "100%",
          width: "35vw",
          minWidth: "35vw",
        }}
      >
        <Box sx={{ padding: "20px" }}>
          <Box
            sx={{
              display: "flex",
              // alignItems: "center",
              flexDirection: "column",
              gap: 0.5,
            }}
          >
            <Typography fontWeight={600} color="text.primary">
              Run Evaluations
            </Typography>
            <HelperText text="Setup custom or pre-defined metrics for your dataset" />
          </Box>
        </Box>
        {errorMessage && (
          <Box sx={{ px: "20px" }}>
            <Alert severity="error" onClose={() => setErrorMessage(null)}>
              {errorMessage}
            </Alert>
          </Box>
        )}
        <Box
          sx={{
            paddingX: "20px",
            gap: "20px",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <EvaluationPresetCard
            onClick={() => {
              setSelectedEval(false);
              setEvaluationTypeOpen(true);
              setConfigureEvalOpen(false);
            }}
            title="+ Create eval"
            subTitle="Select from our preset evaluation metrics or configure your own evaluation metrics"
          />
          <EvaluationPresetCard
            onClick={() => {
              setSelectedEval(false);
              setEvaluationTypeOpen(false);
              setConfigureEvalOpen(true);
            }}
            title="+ Use Saved evals"
            subTitle="Choose from  the list of previously configured evaluation metrics"
          />
        </Box>
        <Divider />
        {renderAddedEvaluations()}
      </Box>
    </>
  );
};

EvaluationListPage.propTypes = {
  onClose: PropTypes.func,
  setEvaluationTypeOpen: PropTypes.func,
  setConfigureEvalOpen: PropTypes.func,
  refreshGrid: PropTypes.func,
  datasetId: PropTypes.string,
  experimentEval: PropTypes.object,
  evalsConfigs: PropTypes.array,
  setSelectedEval: PropTypes.func,
  setEvalsConfigs: PropTypes.func,
  module: PropTypes.string,
  setFormIsDirty: PropTypes.func,
  handleLabelsAdd: PropTypes.func,
  openDrawer: PropTypes.bool,
};

export default EvaluationListPage;
