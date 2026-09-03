import React, { useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Button,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  RadioGroup,
  FormControlLabel,
  Radio,
  FormLabel,
  Stack,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import {
  useCreateSavedView,
  useUpdateSavedView,
  useGetSavedViews,
  getOwnViewNames,
} from "src/api/project/saved-views";
import { useObserveHeader } from "src/sections/project/context/ObserveHeaderContext";
import { useAuthContext } from "src/auth/hooks";
import { enqueueSnackbar } from "notistack";
import { getRequestErrorMessage } from "src/utils/errorUtils";

const TAB_TYPES = [
  { value: "traces", label: "Traces" },
  { value: "spans", label: "Spans" },
  { value: "voice", label: "Voice" },
];

const ViewConfigModal = ({
  open,
  onClose,
  mode = "create",
  initialValues,
  projectId,
  onSuccess,
}) => {
  const [name, setName] = useState("");
  const [tabType, setTabType] = useState("traces");
  const [visibility, setVisibility] = useState("personal");

  const { mutate: createView, isPending: isCreating } =
    useCreateSavedView(projectId);
  const { mutate: updateView, isPending: isUpdating } =
    useUpdateSavedView(projectId);
  const { getViewConfig } = useObserveHeader();
  const { data: savedViewsData } = useGetSavedViews(projectId);

  const isPending = isCreating || isUpdating;

  // Duplicate guard mirroring the backend constraint exactly: per-user,
  // case-sensitive, across all tab types; edit mode excludes the view itself.
  const { user } = useAuthContext();
  const trimmedName = name.trim();
  const ownViewNames = getOwnViewNames(
    (savedViewsData?.custom_views ?? []).filter(
      (v) => v.id !== initialValues?.id,
    ),
    user?.id,
  );
  const isDuplicateName =
    trimmedName.length > 0 && ownViewNames.includes(trimmedName);

  useEffect(() => {
    if (open) {
      setName(initialValues?.name ?? "");
      setTabType(initialValues?.tab_type ?? "traces");
      setVisibility(initialValues?.visibility ?? "personal");
    }
  }, [open, initialValues]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!trimmedName || isDuplicateName) return;

    const snapshot = getViewConfig?.() ?? null;
    const config =
      mode === "edit"
        ? snapshot ?? initialValues?.config ?? {}
        : snapshot ?? {};

    const basePayload = {
      name: trimmedName,
      visibility,
      config,
    };

    if (mode === "edit" && initialValues?.id) {
      updateView(
        { id: initialValues.id, ...basePayload },
        {
          onSuccess: (res) => {
            onClose();
            onSuccess?.(res.data?.result);
          },
          onError: (err) => {
            enqueueSnackbar(getRequestErrorMessage(err, "Failed to update view"), {
              variant: "error",
            });
          },
        },
      );
    } else {
      createView(
        { project_id: projectId, tab_type: tabType, ...basePayload },
        {
          onSuccess: (res) => {
            onClose();
            onSuccess?.(res.data?.result);
          },
          onError: (err) => {
            enqueueSnackbar(getRequestErrorMessage(err, "Failed to create view"), {
              variant: "error",
            });
          },
        },
      );
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="xs"
      fullWidth
      PaperProps={{ component: "form", onSubmit: handleSubmit }}
    >
      <DialogTitle>
        {mode === "edit" ? "Edit View" : "Create New View"}
      </DialogTitle>

      <DialogContent>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          <TextField
            autoFocus
            required
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            size="small"
            fullWidth
            inputProps={{ maxLength: 255 }}
            error={isDuplicateName}
            helperText={
              isDuplicateName ? "A view with this name already exists." : ""
            }
          />

          <FormControl size="small" fullWidth disabled={mode === "edit"}>
            <InputLabel>Type</InputLabel>
            <Select
              value={tabType}
              label="Type"
              onChange={(e) => setTabType(e.target.value)}
            >
              {TAB_TYPES.map((t) => (
                <MenuItem key={t.value} value={t.value}>
                  {t.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <FormControl>
            <FormLabel sx={{ fontSize: 13, mb: 0.5 }}>Visibility</FormLabel>
            <RadioGroup
              value={visibility}
              onChange={(e) => setVisibility(e.target.value)}
              row
            >
              <FormControlLabel
                value="personal"
                control={<Radio size="small" />}
                label="Personal"
                slotProps={{ typography: { variant: "body2" } }}
              />
              <FormControlLabel
                value="project"
                control={<Radio size="small" />}
                label="Shared with team"
                slotProps={{ typography: { variant: "body2" } }}
              />
            </RadioGroup>
          </FormControl>
        </Stack>
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose} size="small">
          Cancel
        </Button>
        <LoadingButton
          type="submit"
          variant="contained"
          size="small"
          loading={isPending}
          disabled={!trimmedName || isDuplicateName}
        >
          {mode === "edit" ? "Save" : "Create"}
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

ViewConfigModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  mode: PropTypes.oneOf(["create", "edit"]),
  initialValues: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    tab_type: PropTypes.string,
    visibility: PropTypes.string,
    config: PropTypes.object,
  }),
  projectId: PropTypes.string.isRequired,
  onSuccess: PropTypes.func,
};

export default React.memo(ViewConfigModal);
