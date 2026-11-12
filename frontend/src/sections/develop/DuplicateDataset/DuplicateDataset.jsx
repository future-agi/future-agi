import { LoadingButton } from "@mui/lab";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  MenuItem,
  Radio,
  RadioGroup,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import HelperText from "src/sections/develop-detail/Common/HelperText";
import axios, { endpoints } from "src/utils/axios";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useWorkspacesList } from "src/api/workspaces/list";
import { isPrototypeMode } from "src/utils/prototype";
import { datasetCaps } from "src/sections/develop/realBackendCaps";
import DatasetTransferDialog from "./DatasetTransferDialog";
import {
  getClonedDatasetInfo,
  planNotes,
  planSentence,
  runsInDataset,
  transferForm,
  workspacesYouCanEdit,
} from "./duplicateDatasetResponse";

const SectionLabel = ({ children }) => (
  <Typography variant="s2" fontWeight="fontWeightMedium" color="text.primary">
    {children}
  </Typography>
);
SectionLabel.propTypes = { children: PropTypes.node };

const DuplicateDatasetDialog = ({ open, onClose, refreshGrid, selected }) => {
  const {
    control,
    handleSubmit: handleFormSubmit,
    reset,
    watch,
  } = useForm({
    defaultValues: {
      name: "",
    },
  });
  const queryClient = useQueryClient();
  const { currentWorkspaceId, switchWorkspace } = useWorkspace();
  const supportsTransfer = isPrototypeMode();
  const { data: workspaceRows } = useWorkspacesList({
    enabled: open && supportsTransfer,
  });
  const workspaces = useMemo(
    () => workspacesYouCanEdit(workspaceRows),
    [workspaceRows],
  );
  const [target, setTarget] = useState(null);
  const [mode, setMode] = useState("copy");
  const [history, setHistory] = useState("latest");
  const [withRuns, setWithRuns] = useState(false);
  const [splitId, setSplitId] = useState("");
  const [checked, setChecked] = useState(false);

  const fieldName = watch("name");
  const datasetName = selected?.name ?? selected?.datasetName ?? "";
  const toWorkspace = target ?? currentWorkspaceId ?? null;
  const toName =
    workspaces.find((w) => w.id === toWorkspace)?.name ?? "this workspace";
  const crossing = Boolean(
    supportsTransfer && toWorkspace && toWorkspace !== currentWorkspaceId,
  );
  const isMove = crossing && mode === "move";
  const options = {
    toWorkspaceId: toWorkspace,
    mode: isMove ? "move" : "copy",
    history: isMove ? "all" : history,
    includeExperiments: isMove || (history === "all" && withRuns),
    splitId: !isMove && history === "latest" ? splitId : "",
  };
  const post = (fields) =>
    axios
      .post(
        endpoints.develop.cloneDataset(selected?.id),
        transferForm({ modelType: selected?.datasetType, ...fields }),
      )
      .then((res) => res.data?.result ?? null);

  // What the dataset carries: its versions, runs and splits (a dry run).
  const { data: whole } = useQuery({
    queryKey: ["clone-dataset-plan", selected?.id, "whole"],
    queryFn: () =>
      post({ history: "all", includeExperiments: true, dryRun: true }),
    // The classic endpoint is a write, not a preview; never call it on open.
    enabled: Boolean(supportsTransfer && open && selected?.id),
    staleTime: 0,
  });
  // Check first: the plan of exactly what the dialog will do.
  const planKey = JSON.stringify(options);
  const {
    data: plan,
    isFetching: isChecking,
    refetch: checkFirst,
  } = useQuery({
    queryKey: ["clone-dataset-plan", selected?.id, planKey],
    queryFn: () => post({ ...options, name: fieldName, dryRun: true }),
    enabled: false,
    staleTime: 0,
  });
  const shownPlan = checked ? plan : null;

  const {
    mutate,
    isPending,
    error,
    reset: resetMutation,
  } = useMutation({
    mutationFn: (data) =>
      axios
        .post(endpoints.develop.cloneDataset(selected?.id), data)
        .then((res) => res.data),
    onSuccess: (data) => {
      const clonedDataset = getClonedDatasetInfo(data);
      trackEvent(Events.duplicateDatasetClicked, {
        [PropertyName.meta]: {
          datasetId: clonedDataset.datasetId,
          datasetName: clonedDataset.datasetName,
        },
      });
      const to = data?.result?.to_workspace_id ?? toWorkspace;
      const where = data?.result?.to_workspace_name ?? toName;
      if (to && to !== currentWorkspaceId) {
        const switchTo = () => switchWorkspace(to, currentWorkspaceId);
        enqueueSnackbar(
          `${data?.result?.mode === "move" ? "Moved" : "Copied"} to ${where}`,
          {
            variant: "success",
            action: () => (
              <Button
                size="small"
                color="inherit"
                onClick={switchTo}
                sx={{ fontWeight: 600 }}
              >
                Switch workspace
              </Button>
            ),
          },
        );
      } else {
        enqueueSnackbar(`${fieldName} dataset has been created`, {
          variant: "success",
        });
      }
      queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      handleClose();
      refreshGrid?.();
    },
  });

  const onSubmitCreateColumn = (data) => {
    const name = data.name?.trim();
    if (!isMove && !name) {
      enqueueSnackbar("Please enter a name", { variant: "error" });
      return;
    }
    if (supportsTransfer) {
      mutate(
        transferForm({
          modelType: selected?.datasetType,
          name: isMove ? datasetName : name,
          ...options,
        }),
      );
    } else {
      const form = new FormData();
      form.append("new_dataset_name", name);
      mutate(form);
    }
  };

  const handleClose = () => {
    reset();
    resetMutation();
    setTarget(null);
    setMode("copy");
    setHistory("latest");
    setWithRuns(false);
    setSplitId("");
    setChecked(false);
    onClose();
  };
  // Any change makes the last plan and the last refusal stale.
  const changed = (fn) => (event) => {
    setChecked(false);
    resetMutation();
    fn(event.target.value, event);
  };
  const versions = whole?.plan?.versions;
  const runs = runsInDataset(whole);
  const splits = whole?.source_splits ?? [];
  const refused = shownPlan?.refused ?? null;
  const serverError =
    error?.result ?? error?.response?.data?.result ?? error?.message ?? null;

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <Box sx={{ padding: 2 }}>
        <DialogTitle sx={{ padding: 0, margin: 0, gap: "2px" }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Typography
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              variant="m2"
            >
              {datasetName
                ? `${supportsTransfer ? "Duplicate or move" : "Duplicate"} “${datasetName}”`
                : "Duplicate Dataset"}
            </Typography>
            <IconButton onClick={handleClose} aria-label="Close">
              <Iconify icon="solar:close-linear" color="text.primary" />
            </IconButton>
          </Box>
          <HelperText
            text={
              supportsTransfer
                ? "Copy it with its history, or move it to another workspace"
                : "Copy rows and input columns into this workspace."
            }
          />
        </DialogTitle>
        <Box component="form" onSubmit={handleFormSubmit(onSubmitCreateColumn)}>
          <DialogContent
            sx={{
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: "16px",
              paddingTop: 2,
            }}
          >
            {!isMove && (
              <FormTextFieldV2
                autoFocus
                placeholder="Enter dataset name"
                label="Name"
                size="small"
                control={control}
                fieldName="name"
                fullWidth
              />
            )}
            {supportsTransfer && workspaces.length > 1 && (
              <TextField
                select
                size="small"
                label="To workspace"
                value={toWorkspace ?? ""}
                onChange={changed((v) => {
                  setTarget(v);
                  if (v === currentWorkspaceId) setMode("copy");
                })}
                fullWidth
              >
                {workspaces.map((w) => (
                  <MenuItem key={w.id} value={w.id}>
                    {w.name}
                    {w.id === currentWorkspaceId ? " (this workspace)" : ""}
                  </MenuItem>
                ))}
              </TextField>
            )}
            {crossing && (
              <RadioGroup
                row
                aria-label="Copy or move"
                value={mode}
                onChange={changed(setMode)}
              >
                <FormControlLabel
                  value="copy"
                  control={<Radio />}
                  label="Copy"
                />
                <FormControlLabel
                  value="move"
                  control={<Radio />}
                  label="Move"
                />
              </RadioGroup>
            )}
            {supportsTransfer &&
              (isMove ? (
                <Typography variant="s2" color="text.secondary">
                  Move keeps the dataset and its link. Every version
                  {versions ? ` (${versions})` : ""}, its splits and its
                  experiments and runs{runs != null ? ` (${runs} runs)` : ""} go
                  with it.
                </Typography>
              ) : (
                <Box
                  sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}
                >
                  <SectionLabel>History</SectionLabel>
                  <RadioGroup
                    aria-label="History"
                    value={history}
                    onChange={changed(setHistory)}
                  >
                    <FormControlLabel
                      value="latest"
                      control={<Radio />}
                      label="Latest version only"
                    />
                    <FormControlLabel
                      value="all"
                      control={<Radio />}
                      label={`Every version${versions ? ` (${versions})` : ""}`}
                    />
                  </RadioGroup>
                  {history === "latest" && splits.length > 0 && (
                    <TextField
                      select
                      size="small"
                      label="Rows"
                      value={splitId}
                      onChange={changed(setSplitId)}
                      sx={{ maxWidth: 320 }}
                      SelectProps={{ displayEmpty: true }}
                      InputLabelProps={{ shrink: true }}
                    >
                      <MenuItem value="">All rows</MenuItem>
                      {splits.map((s) => (
                        <MenuItem key={s.id} value={s.id}>
                          Split {s.name} ({s.rows} rows)
                        </MenuItem>
                      ))}
                    </TextField>
                  )}
                  {history === "all" && runs > 0 && (
                    <FormControlLabel
                      control={
                        <Checkbox
                          checked={withRuns}
                          onChange={(e) => {
                            setChecked(false);
                            resetMutation();
                            setWithRuns(e.target.checked);
                          }}
                        />
                      }
                      label={`Experiments and runs (${runs} runs)`}
                    />
                  )}
                </Box>
              ))}
            {supportsTransfer && shownPlan && !refused && (
              <Alert severity="info" icon={false} data-testid="clone-plan">
                {planSentence(shownPlan, {
                  move: isMove,
                  workspace: shownPlan.to_workspace_name ?? toName,
                })}
                {planNotes(shownPlan).map((note) => (
                  <Typography
                    key={note}
                    variant="s2"
                    component="p"
                    sx={{ marginTop: 0.5 }}
                  >
                    {note}
                  </Typography>
                ))}
              </Alert>
            )}
            {(refused || serverError) && (
              <Alert severity="error">{refused ?? String(serverError)}</Alert>
            )}
          </DialogContent>
          <DialogActions sx={{ padding: 0, marginTop: 3 }}>
            <Button onClick={handleClose} variant="outlined">
              <Typography
                variant="s2"
                fontWeight={"fontWeightSemiBold"}
                color="text.secondary"
              >
                Cancel
              </Typography>
            </Button>
            {supportsTransfer && (
              <LoadingButton
                variant="outlined"
                loading={isChecking}
                onClick={() => {
                  resetMutation();
                  setChecked(true);
                  checkFirst();
                }}
              >
                <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
                  Check first
                </Typography>
              </LoadingButton>
            )}
            <LoadingButton
              variant="contained"
              color="primary"
              loading={isPending}
              type="submit"
            >
              <Typography variant="s2" fontWeight={"fontWeightSemiBold"}>
                {isMove ? "Move" : "Copy"}
              </Typography>
            </LoadingButton>
          </DialogActions>
        </Box>
      </Box>
    </Dialog>
  );
};

DuplicateDatasetDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  refreshGrid: PropTypes.func,
  selected: PropTypes.any,
};

/** The real backend offers workspace copy and move only with its capability. */
export const realTransferShown = () => {
  const caps = datasetCaps();
  return !isPrototypeMode() && caps.real && caps.duplicateMove;
};

const DuplicateDataset = (props) =>
  realTransferShown() ? (
    <DatasetTransferDialog {...props} />
  ) : (
    <DuplicateDatasetDialog {...props} />
  );

DuplicateDataset.propTypes = DuplicateDatasetDialog.propTypes;

export default DuplicateDataset;
