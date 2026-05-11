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
} from "src/api/project/saved-views";
import { useObserveHeader } from "src/sections/project/context/ObserveHeaderContext";

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

  const isPending = isCreating || isUpdating;

  useEffect(() => {
    if (open) {
      setName(initialValues?.name ?? "");
      setTabType(initialValues?.tab_type ?? "traces");
      setVisibility(initialValues?.visibility ?? "personal");
    }
  }, [open, initialValues]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!name.trim()) return;

    const snapshot = getViewConfig?.() ?? null;
    const config =
      mode === "edit"
        ? snapshot ?? initialValues?.config ?? {}
        : snapshot ?? {};

    const payload = {
      name: name.trim(),
      tab_type: tabType,
      visibility,
      config,
    };

    if (mode === "edit" && initialValues?.id) {
      updateView(
        { id: initialValues.id, ...payload },
        {
          onSuccess: (res) => {
            onClose();
            onSuccess?.(res.data?.result);
          },
        },
      );
    } else {
      createView(
        { project_id: projectId, ...payload },
        {
          onSuccess: (res) => {
            onClose();
            onSuccess?.(res.data?.result);
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
          disabled={!name.trim()}
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
