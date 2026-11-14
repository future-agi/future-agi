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
import { useMutation, useQuery } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import LoadingButton from "@mui/lab/LoadingButton";
import ConfirmRunEvaluations from "../Common/AddEvaluation/ConfirmRunEvaluations";
import { ShowComponent } from "src/components/show";
import { camelCaseToTitleCase } from "src/utils/utils";
import PromptEvaluationListPage from "./PromptEvaluationListPage";
import HelperText from "../Common/HelperText";

const CompareEvaluationListPage = ({
  onClose,
  setEvaluationTypeOpen,
  setConfigureEvalOpen,
  datasetId,
  experimentEval,
  setSelectedEval,
  module,
  evalsConfigs,
  setEvalsConfigs,
  handleLabelsAdd,
  setFormIsDirty,
  selectedDatasets,
  isCompareDataset,
  compareRefreshGrid,
}) => {
  const hasRun = useRef(false);
  const [selectedUserEvalList, setSelectedUserEvalList] = useState([]);
  const [confirmRunEvaluationsOpen, setConfirmRunEvaluationsOpen] =
    useState(false);
  const [errorMessage, setErrorMessage] = useState(null);
  const [, setOpen] = useState(null);

  const { mutate: runComparisonEvals, isPending: startingComparisonEvals } =
    useMutation({
      mutationFn: (d) =>
        axios.post(endpoints.develop.eval.compareRunEvals(datasetId), d),
      onSuccess: () => {
        enqueueSnackbar("Evaluations started successfully", {
          variant: "success",
        });
        compareRefreshGrid({ route: [] }, true);
        onClose();
      },
      onError: (error) => {
        setErrorMessage(
          error?.response?.data?.message || "Failed to run evaluations",
        );
      },
    });

  const startingEvals = startingComparisonEvals;

  const { data: compareDatasetEvalList } = useQuery({
    queryFn: () =>
      axios.post(endpoints.develop.eval.getCompareEvalsList(), {
        eval_type: "user",
        dataset_ids: selectedDatasets,
      }),
    queryKey: [
      "develop",
      "compare-user-eval-list",
      selectedDatasets,
      isCompareDataset,
    ],
    select: (d) => d?.data?.result?.evals,
    enabled: Boolean(isCompareDataset && selectedDatasets?.length),
  });

  const userEvalList = compareDatasetEvalList;

  useEffect(() => {
    if (!hasRun.current && userEvalList?.length) {
      if (selectedUserEvalList?.length !== userEvalList.length) {
        setSelectedUserEvalList(userEvalList);
        setEvalsConfigs(userEvalList);
        hasRun.current = true;
      }
    }
  }, [userEvalList, isCompareDataset]);

  useEffect(() => {
    // Reset hasRun when isCompareDataset changes
    hasRun.current = false;
  }, [isCompareDataset]);

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
                isCompare={true}
                selectedDatasets={selectedDatasets}
                selected={selectedUserEvalList?.some(
                  (e) => e.id === eachUserEval?.id,
                )}
                experimentEval={experimentEval}
                refreshGrid={compareRefreshGrid}
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
                showDeleteButton={false}
                onClose={onClose}
              />
            ))}
            {/* <DeleteEval
              open={Boolean(open)}
              setOpen={(status) => {
                setOpen(null);
              }}
              dataset={datasetId}
              refreshGrid={() => { refreshGrid(); refetch(); }}
              userEval={open}
            /> */}
          </Box>
        </Box>
        <ConfirmRunEvaluations
          open={confirmRunEvaluationsOpen}
          onClose={() => setConfirmRunEvaluationsOpen(false)}
          onConfirm={() => {
            setConfirmRunEvaluationsOpen(false);
            runComparisonEvals({
              user_eval_names: selectedUserEvalList.map((e) => e.name),
              dataset_ids: selectedDatasets,
            });
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
            onClick={() => setConfirmRunEvaluationsOpen(true)}
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
            <Typography fontWeight={600} color="text.secondary">
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

CompareEvaluationListPage.propTypes = {
  compareRefreshGrid: PropTypes.func,
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
  selectedDatasets: PropTypes.array,
  isCompareDataset: PropTypes.bool,
};

export default CompareEvaluationListPage;
